import asyncio
import csv
import io
import json
from pathlib import Path
from .config import MAX_BATCH_ROWS, MAX_FILE_BYTES
from .sentiment import validate_text


def parse_file(filename, content):
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('Размер файла превышает 1 МБ.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Сохраните файл в кодировке UTF-8.')
    suffix = Path(filename).suffix.lower()
    if suffix == '.txt':
        rows = [line for line in text.splitlines() if line.strip()]
    elif suffix == '.csv':
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames or 'text' not in reader.fieldnames:
            raise ValueError('В CSV обязателен заголовок столбца text.')
        rows = [row.get('text') for row in reader]
    elif suffix == '.json':
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError('Некорректный JSON.')
        if not isinstance(value, list):
            raise ValueError('JSON должен содержать массив строк или объектов с полем text.')
        rows = [r.get('text') if isinstance(r, dict) else r for r in value]
    else:
        raise ValueError('Поддерживаются только TXT, CSV и JSON.')
    if not rows:
        raise ValueError('В файле нет комментариев.')
    if len(rows) > MAX_BATCH_ROWS:
        raise ValueError(f'Максимум {MAX_BATCH_ROWS} комментариев в одном файле.')
    result = []
    for index, value in enumerate(rows, 1):
        try:
            result.append(validate_text(value))
        except ValueError as exc:
            raise ValueError(f'Комментарий {index}: {exc}')
    return result


class BatchProcessor:
    def __init__(self, repository, analyzer, publish):
        self.repository = repository
        self.analyzer = analyzer
        self.publish = publish
        self.queue = asyncio.Queue(maxsize=20)
        self.worker = None

    def start(self):
        self.worker = asyncio.create_task(self._run())

    async def process_batch(self, uid, filename, content):
        if not self.analyzer.ready:
            raise RuntimeError(self.analyzer.error)
        texts = parse_file(filename, content)
        if self.queue.full():
            raise ValueError('Очередь заполнена. Повторите загрузку позже.')
        if sum(j['status'] in ('queued', 'running') for j in self.repository.jobs(uid)) >= 3:
            raise ValueError('Для одного пользователя разрешено три незавершённых задания.')
        job = self.repository.create_job(uid, Path(filename.replace('\\', '/')).name[:160], len(texts))
        self.queue.put_nowait((job, texts))
        return job

    async def _run(self):
        while True:
            job, texts = await self.queue.get()
            try:
                if not self.repository.active_user(job['user_id']):
                    continue
                self.repository.update_job(job['id'], 'running')
                analyses = []
                for start in range(0, len(texts), 8):
                    results = await self.analyzer.analyze_many(texts[start:start + 8])
                    if not self.repository.active_user(job['user_id']):
                        raise ValueError('Аккаунт удалён.')
                    analyses.extend(results)
                    self.repository.update_job(job['id'], 'running', len(analyses))
                    await self.publish({'type': 'batch_progress', 'job': self.repository.job(job['id'], job['user_id'])}, job['user_id'])
                    await asyncio.sleep(0)
                self.repository.complete_job(job, texts, analyses)
                await self.publish({'type': 'batch_progress', 'job': self.repository.job(job['id'], job['user_id'])}, job['user_id'])
                await self.publish({'type': 'stats', 'stats': self.repository.get_chat_stats()})
            except asyncio.CancelledError:
                self.repository.update_job(job['id'], 'failed', error='Сервер остановлен. Загрузите файл повторно.')
                raise
            except Exception:
                self.repository.update_job(job['id'], 'failed', error='Ошибка обработки. Проверьте модель и повторите загрузку.')
                await self.publish({'type': 'batch_progress', 'job': self.repository.job(job['id'], job['user_id'])}, job['user_id'])
            finally:
                self.queue.task_done()

    async def close(self):
        self.worker.cancel()
        try:
            await self.worker
        except asyncio.CancelledError:
            pass
