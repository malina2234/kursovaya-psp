import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import threading
import time

from .config import ROOT, DATA_DIR

URL_PATTERN = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\b')
CLOUDFLARED_SHA256 = '86aee4017b26625cee8484c113558f48effa4cd47f7aa05fcf425604e5d2b23c'


def instance_id(data_dir=DATA_DIR):
    value=f'{ROOT.resolve()}|{Path(data_dir).resolve()}'.casefold()
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def lan_addresses(port, listen_host):
    if listen_host not in ('0.0.0.0', '::'):
        return []
    try:
        addresses={item[4][0] for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)}
    except OSError:
        addresses=set()
    # исключаем loopback, link-local и адреса VPN из диапазона 100.64.0.0/10
    addresses={a for a in addresses if ipaddress.ip_address(a).is_private
               and not ipaddress.ip_address(a).is_loopback and not ipaddress.ip_address(a).is_link_local}
    return [f'http://{address}:{port}' for address in sorted(addresses)]


class InternetAccess:
    def __init__(self, port, data_dir=DATA_DIR, enabled=False, binary=None):
        self.port=port
        self.data_dir=Path(data_dir)
        self.enabled=enabled
        self.binary=Path(binary or ROOT/'tools'/'cloudflared.exe')
        self.expected_id=instance_id(self.data_dir)
        self.lock=threading.Lock()
        self.stop_event=threading.Event()
        self.process=None
        self.thread=None
        self.status='starting' if enabled else 'disabled'
        self.url=None
        self.error=None
        self.candidate=None
        self.registered=False

    def snapshot(self):
        with self.lock:
            return {'enabled':self.enabled,'status':self.status,'url':self.url,'error':self.error}

    def _set(self, status, url=None, error=None):
        with self.lock:
            self.status=status
            self.url=url
            self.error=error
        if self.enabled:
            self.data_dir.mkdir(parents=True,exist_ok=True)
            content=(f'Ссылка для телефона и других компьютеров:\n{url}\n\n'
                     'Работает, пока запущены программа и туннель. При следующем запуске адрес изменится.\n') if url else (error or 'Интернет-ссылка пока недоступна. Откройте программу и проверьте статус подключения.')
            (self.data_dir/'internet_link.txt').write_text(content,encoding='utf-8')

    def start(self):
        if self.enabled:
            self.thread=threading.Thread(target=self._run,daemon=True,name='internet-access')
            self.thread.start()

    def _accept_line(self,line):
        match=URL_PATTERN.search(line)
        if match:self.candidate=match.group(0)
        if 'Registered tunnel connection' in line:self.registered=True

    def _read_output(self, logfile):
        for line in self.process.stdout:
            with logfile.open('a',encoding='utf-8') as out:out.write(line)
            self._accept_line(line)

    def _run(self):
        try:
            self._set('starting')
            if not self.binary.is_file():raise RuntimeError('Не найдена tools/cloudflared.exe. Запустите setup_internet.bat.')
            if hashlib.sha256(self.binary.read_bytes()).hexdigest()!=CLOUDFLARED_SHA256:
                raise RuntimeError('Контрольная сумма cloudflared не совпадает. Запустите setup_internet.bat.')
            logdir=self.data_dir/'logs'
            logdir.mkdir(parents=True,exist_ok=True)
            logfile=logdir/f'cloudflared-{int(time.time())}.log'
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            command=[str(self.binary),'tunnel','--no-autoupdate','--protocol','http2',
                     '--url',f'http://127.0.0.1:{self.port}','--metrics','127.0.0.1:0']
            self.process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                          text=True,encoding='utf-8',errors='replace',creationflags=flags)
            reader=threading.Thread(target=self._read_output,args=(logfile,),daemon=True)
            reader.start()
            self._set('connecting')
            started=time.monotonic()
            while not self.stop_event.wait(.5):
                if self.process.poll() is not None:
                    raise RuntimeError('Туннель завершился. Проверьте интернет и data/logs/cloudflared-*.log. Локальный чат продолжает работать.')
                # готовность подтверждает коннектор; локальный DNS может обновляться
                # медленнее, чем на клиентском устройстве в другой сети
                if self.candidate and self.registered:
                    self._set('online',self.candidate)
                    print(f'\nССЫЛКА ДЛЯ ТЕЛЕФОНА: {self.candidate}\nОна также доступна по кнопке «Ссылка на чат».\n',flush=True)
                    break
                if time.monotonic()-started>75:
                    raise RuntimeError('Не удалось открыть интернет-ссылку за 75 секунд. Проверьте интернет, VPN и журнал туннеля. Локальный чат продолжает работать.')
            while not self.stop_event.wait(2):
                if self.process.poll() is not None:
                    raise RuntimeError('Интернет-туннель остановлен. Локальный чат продолжает работать. Для новой ссылки перезапустите start_internet.bat.')
        except Exception as exc:
            self._set('error',error=str(exc))
            print(f'Интернет-доступ: {exc}',flush=True)
        finally:
            self._terminate()

    def _terminate(self):
        process=self.process
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=12)
        self._terminate()
        if self.enabled:self._set('stopped',error='Туннель остановлен. Для новой ссылки запустите start_internet.bat.')
