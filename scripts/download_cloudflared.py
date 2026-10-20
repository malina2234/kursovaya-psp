import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.access import CLOUDFLARED_SHA256
VERSION='2026.10.0'
URL=f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/cloudflared-windows-amd64.exe'

def download():
    folder=ROOT/'tools'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'cloudflared.exe'
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==CLOUDFLARED_SHA256:
        print('cloudflared is ready.')
        return
    temporary=folder/'cloudflared.download'
    try:
        with urllib.request.urlopen(URL,timeout=120) as response,temporary.open('wb') as out:
            while block:=response.read(1024*1024):out.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=CLOUDFLARED_SHA256:
            raise RuntimeError('SHA-256 verification failed.')
        temporary.replace(path)
        (folder/'cloudflared_manifest.json').write_text(json.dumps({'version':VERSION,'url':URL,'sha256':CLOUDFLARED_SHA256},indent=2),encoding='utf-8')
        print('cloudflared downloaded and verified.')
    finally:
        temporary.unlink(missing_ok=True)

if __name__=='__main__':
    try:download()
    except Exception as exc:
        print(f'Download failed: {exc}',file=sys.stderr)
        sys.exit(1)
