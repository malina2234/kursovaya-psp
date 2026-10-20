import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from .config import MODEL_DIR, MAX_TEXT


def validate_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Сообщение не должно быть пустым.')
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise ValueError(f'Максимум {MAX_TEXT} символов в одном комментарии.')
    return text


class SentimentAnalyzer:
    """общая ONNX-сессия; блокировка защищает токенизатор от одновременных изменений"""
    def __init__(self, model_dir=MODEL_DIR):
        self.model_dir = model_dir
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='sentiment')
        self.lock = threading.Lock()
        self.slots = asyncio.Semaphore(2)
        self.error = None
        self.session = None
        self.tokenizer = None

    def load(self):
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(self.model_dir / 'model.onnx'),
                                               sess_options=options, providers=['CPUExecutionProvider'])
            self.tokenizer = Tokenizer.from_file(str(self.model_dir / 'tokenizer.json'))
            self.tokenizer.enable_truncation(max_length=512)
            self.tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
            config = json.loads((self.model_dir / 'config.json').read_text(encoding='utf-8'))
            self.labels = [config['id2label'][str(i)] for i in range(3)]
            if set(self.labels) != {'negative', 'neutral', 'positive'}:
                raise ValueError('Неверный набор классов модели.')
            self.error = None
        except Exception:
            self.session = None
            self.error = 'Модель недоступна. Выполните python scripts/download_model.py и перезапустите сервер.'

    @property
    def ready(self):
        return self.session is not None and self.error is None

    def _predict(self, texts):
        with self.lock:
            encoded = self.tokenizer.encode_batch(texts)
        values = {
            'input_ids': np.array([e.ids for e in encoded], dtype=np.int64),
            'attention_mask': np.array([e.attention_mask for e in encoded], dtype=np.int64),
            'token_type_ids': np.array([e.type_ids for e in encoded], dtype=np.int64),
        }
        feed = {i.name: values[i.name] for i in self.session.get_inputs()}
        logits = self.session.run(None, feed)[0]
        # используем sigmoid отдельно для каждого класса; сумма оценок не обязана равняться 1
        scores = 1.0 / (1.0 + np.exp(-np.clip(logits, -60, 60)))
        result = []
        for row, encoding in zip(scores, encoded):
            index = int(row.argmax())
            result.append({'label': self.labels[index], 'confidence': round(float(row[index]), 6),
                           'scores': {label: round(float(row[i]), 6) for i, label in enumerate(self.labels)},
                           'truncated': bool(encoding.overflowing), 'model': 'rubert-tiny-sentiment-balanced'})
        return result

    async def analyze_many(self, texts):
        texts = [validate_text(t) for t in texts]
        if not self.ready:
            raise RuntimeError(self.error or 'Модель ещё загружается.')
        async with self.slots:
            return await asyncio.get_running_loop().run_in_executor(self.pool, self._predict, texts)

    async def analyze_message(self, text):
        return (await self.analyze_many([text]))[0]

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
