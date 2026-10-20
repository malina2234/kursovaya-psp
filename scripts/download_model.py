import hashlib
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import MODEL_DIR, MODEL_REPO, MODEL_REVISION

FILES = {'onnx/model.onnx': 'model.onnx', 'tokenizer.json': 'tokenizer.json',
         'config.json': 'config.json', 'README.md': 'MODEL_CARD.md'}

def download():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {'repository': MODEL_REPO, 'revision': MODEL_REVISION, 'files': {}}
    for remote, local in FILES.items():
        dest = MODEL_DIR / local
        if not dest.exists():
            print(f'Downloading {remote}...', flush=True)
            url = f'https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{remote}'
            req = urllib.request.Request(url, headers={'User-Agent': 'CourseChat/1.0'})
            part = dest.with_suffix(dest.suffix + '.part')
            try:
                with urllib.request.urlopen(req, timeout=120) as response, part.open('wb') as out:
                    while block := response.read(1024 * 1024):
                        out.write(block)
                part.replace(dest)
            finally:
                part.unlink(missing_ok=True)
        manifest['files'][local] = hashlib.sha256(dest.read_bytes()).hexdigest()
    (MODEL_DIR / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Model ready. Analysis works offline.', flush=True)

if __name__ == '__main__':
    try:
        download()
    except Exception as exc:
        print(f'Download failed: {exc}\nRun scripts/download_model.py again.', file=sys.stderr)
        sys.exit(1)
