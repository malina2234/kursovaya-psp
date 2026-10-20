import argparse
import os
import threading
import time
from pathlib import Path
import webbrowser
import json
import urllib.request
import urllib.error
import socket
from uuid import uuid4
import uvicorn


def main():
    parser = argparse.ArgumentParser(description='Централизованный чат с анализом тональности')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--internet', action='store_true', help='Wi-Fi и временная HTTPS-ссылка')
    parser.add_argument('--data-dir', help='Отдельная папка SQLite, например для демонстрации')
    args = parser.parse_args()
    if args.data_dir:
        os.environ['CHAT_DATA_DIR'] = args.data_dir
    if args.internet:
        args.host = '0.0.0.0'
    from app.config import DATA_DIR
    from app.access import InternetAccess, instance_id
    host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    url = f'http://{host}:{args.port}/?v=2.5&launch={time.time_ns()}'
    # повторное создание репозитория прервёт текущие пакетные задания
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    existing = None
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:
            probe.bind((args.host,args.port))
            port_free=True
        except OSError:
            port_free=False
    if not port_free:
        try:
            with opener.open(f'http://{host}:{args.port}/api/health', timeout=3) as response:
                existing = json.load(response)
        except (OSError, ValueError, urllib.error.URLError):
            print('Порт занят или недоступен. Закройте прежнее окно сервера и повторите запуск.', flush=True)
            return
    if existing is not None:
        if not isinstance(existing,dict):
            print('На этом порту работает другая программа.',flush=True)
            return
        same = existing.get('instance_id') == instance_id(DATA_DIR)
        needs_network = args.host in ('0.0.0.0','::') and existing.get('listen_host') not in ('0.0.0.0','::')
        needs_internet = args.internet and (not existing.get('internet_enabled') or existing.get('internet_status') in ('error','stopped'))
        needs_update = existing.get('version') != '2.5.0'
        if not same or needs_network or needs_internet or needs_update:
            print('На этом порту уже работает другой режим или старая версия чата.\n'
                  'Остановите прежний сервер (Ctrl+C в его окне), затем запустите выбранный файл ещё раз.', flush=True)
            return
        print('Чат уже работает. Используем запущенный сервер.', flush=True)
        if not args.no_browser: webbrowser.open(url)
        return
    from app.server import create_app
    app = create_app()
    access = InternetAccess(args.port, DATA_DIR, enabled=args.internet)
    app.state.internet_access = access
    app.state.listen_host = args.host
    app.state.port = args.port
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port,
                            ws_max_size=16_384, ws_ping_interval=20, ws_ping_timeout=20))
    stopped = threading.Event()
    runtime_path = Path(DATA_DIR)/'server_runtime.json'
    stop_path = Path(DATA_DIR)/'stop_server.request'
    control_token = uuid4().hex
    runtime_path.parent.mkdir(parents=True,exist_ok=True)
    runtime_path.write_text(json.dumps({'pid':os.getpid(),'instance_id':instance_id(DATA_DIR),
                            'control_token':control_token,'port':args.port}),encoding='utf-8')
    def watch_stop_request():
        while not stopped.wait(.5):
            try:
                request=json.loads(stop_path.read_text(encoding='utf-8'))
                if request.get('control_token')==control_token:
                    server.should_exit=True
                    return
            except (OSError,ValueError):
                pass
    threading.Thread(target=watch_stop_request,daemon=True,name='local-stop-control').start()
    def start_when_ready():
        while not stopped.wait(0.1):
            if server.started:
                access.start()
                if not args.no_browser: webbrowser.open(url)
                return
    threading.Thread(target=start_when_ready, daemon=True, name='open-browser').start()
    print(f'Папка программы: {Path(__file__).resolve().parent}', flush=True)
    print('Версия 2.5: отдельная статистика и диаграмма для загруженных файлов.', flush=True)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        access.close()
        try:
            if json.loads(runtime_path.read_text(encoding='utf-8')).get('control_token')==control_token:
                runtime_path.unlink(missing_ok=True)
                stop_path.unlink(missing_ok=True)
        except (OSError,ValueError):
            pass


if __name__ == '__main__':
    main()
