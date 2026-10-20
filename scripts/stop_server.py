import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.access import instance_id

def stop(data_dir):
    folder=Path(data_dir)
    runtime=folder/'server_runtime.json'
    if not runtime.exists():
        print('Сервер уже остановлен или запущен прежней версией. Для прежней версии нажмите Ctrl+C в его окне.')
        return False
    state=json.loads(runtime.read_text(encoding='utf-8'))
    if state.get('instance_id')!=instance_id(folder):
        raise RuntimeError('Этот файл состояния принадлежит другой копии программы.')
    temporary=folder/'stop_server.request.tmp'
    temporary.write_text(json.dumps({'control_token':state['control_token']}),encoding='utf-8')
    temporary.replace(folder/'stop_server.request')
    for _ in range(100):
        if not runtime.exists():
            print('Сервер и интернет-туннель остановлены.')
            return True
        time.sleep(.2)
    print('Остановка ещё выполняется. Если окно зависло, нажмите Ctrl+C в окне сервера.')
    return False

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    args=parser.parse_args()
    try:stop(args.data_dir)
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'Не удалось остановить сервер: {exc}',file=sys.stderr)
        sys.exit(1)
