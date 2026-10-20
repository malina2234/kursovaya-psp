import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv('CHAT_DATA_DIR', str(ROOT / 'data')))
MODEL_DIR = ROOT / 'models' / 'rubert-tiny-sentiment'
MODEL_REPO = 'onnx-community/rubert-tiny-sentiment-balanced-ONNX'
MODEL_REVISION = '822213a162cb33e1ed632625a4433d327f0d6e10'
MAX_TEXT = 2000
MAX_FILE_BYTES = 1024 * 1024
MAX_BATCH_ROWS = 1000
MIN_PROFILE_MESSAGES = 5
PROFILE_THRESHOLD = 0.60
