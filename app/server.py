import asyncio
from contextlib import asynccontextmanager
import csv
import io
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .batch import BatchProcessor
from .config import DATA_DIR, ROOT, MAX_FILE_BYTES, MAX_TEXT
from .connections import ConnectionManager
from .sentiment import SentimentAnalyzer, validate_text
from .storage import Repository
from .access import instance_id, lan_addresses

log = logging.getLogger('coursechat')


class AuthRequest(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class ConversationRequest(BaseModel):
    peer_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default='', max_length=80)


class ClearGeneralRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmed: bool = False


class DeleteUserRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmed: bool = False


class ReadRequest(BaseModel):
    last_id: int = Field(ge=0)


def create_app(data_dir=DATA_DIR, analyzer=None):
    manager = ConnectionManager()
    repository = Repository(Path(data_dir) / 'chat.sqlite3')
    analyzer = analyzer or SentimentAnalyzer()
    batch = BatchProcessor(repository, analyzer, manager.broadcast)
    auth_attempts = defaultdict(deque)
    chat_attempts = defaultdict(deque)
    clear_attempts = defaultdict(deque)
    delete_attempts = defaultdict(deque)

    def rate_limit(storage, key, maximum, window):
        current = time.monotonic()
        events = storage[key]
        while events and current - events[0] > window:
            events.popleft()
        if len(events) >= maximum:
            return False
        events.append(current)
        return True

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(analyzer.load)
        if not analyzer.ready:
            log.warning(analyzer.error)
        batch.start()
        try:
            yield
        finally:
            await batch.close()
            await manager.close()
            await asyncio.to_thread(analyzer.close)

    app = FastAPI(title='Чат и аналитика тональности', version='2.5.0', lifespan=lifespan)
    app.state.repository = repository
    app.state.analyzer = analyzer
    app.state.batch = batch
    app.state.instance_id = instance_id(data_dir)
    app.state.listen_host = '127.0.0.1'
    app.state.port = 8000
    app.state.internet_access = None
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def fresh_interface(request, call_next):
        response = await call_next(request)
        if request.url.path == '/' or request.url.path.startswith(('/static/', '/api/')):
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    def user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        result = repository.user_by_token(credentials.credentials) if credentials else None
        if not result:
            raise HTTPException(401, 'Войдите в приложение.')
        return result

    def ensure_model():
        if not analyzer.ready:
            raise HTTPException(503, analyzer.error)

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'static' / 'index.html')

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'model_ready': analyzer.ready, 'model_error': analyzer.error,
                'model': 'rubert-tiny-sentiment-balanced', 'online': manager.online, 'version': '2.5.0',
                'application': 'coursechat', 'instance_id': app.state.instance_id,
                'listen_host': app.state.listen_host,
                'internet_enabled': bool(app.state.internet_access and app.state.internet_access.enabled),
                'internet_status': app.state.internet_access.snapshot()['status'] if app.state.internet_access else 'disabled'}

    @app.get('/api/access')
    async def access_info(current=Depends(user)):
        access = app.state.internet_access
        return {'local_url': f'http://127.0.0.1:{app.state.port}',
                'lan_urls': await asyncio.to_thread(lan_addresses, app.state.port, app.state.listen_host),
                'lan_enabled': app.state.listen_host in ('0.0.0.0','::'),
                'internet': access.snapshot() if access else
                    {'enabled': False, 'status': 'disabled', 'url': None, 'error': None}}

    @app.post('/api/auth/{action}')
    async def auth(action: str, request: AuthRequest):
        if action not in ('login', 'register'):
            raise HTTPException(404, 'Неизвестное действие.')
        if not rate_limit(auth_attempts, request.name.strip().casefold(), 12, 60):
            raise HTTPException(429, 'Слишком много попыток входа. Подождите минуту.')
        try:
            return await asyncio.to_thread(repository.authenticate, request.name, request.password, action == 'register')
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/logout')
    async def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer), current=Depends(user)):
        repository.revoke(credentials.credentials)
        return {'ok': True}

    @app.get('/api/me')
    async def me(current=Depends(user)):
        return current

    @app.get('/api/history')
    async def history(before: int | None = Query(None, ge=1), conversation_id: str = 'general', current=Depends(user)):
        if not repository.can_access(conversation_id, current['id']):
            raise HTTPException(404, 'Диалог не найден.')
        return repository.history(before, conversation_id=conversation_id)

    @app.post('/api/general/clear')
    async def clear_general(request: ClearGeneralRequest, current=Depends(user)):
        if not current.get('is_admin'):
            raise HTTPException(403, 'Очищать общий чат может только администратор.')
        if not request.confirmed:
            raise HTTPException(400, 'Подтвердите удаление общего чата для всех участников.')
        if not rate_limit(clear_attempts, current['id'], 5, 60):
            raise HTTPException(429, 'Слишком много попыток очистки. Подождите минуту.')
        try:
            result = await asyncio.to_thread(repository.clear_general, current['id'], request.password)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(403, str(exc))
        await manager.broadcast({'type': 'history_cleared', **result})
        await manager.broadcast({'type': 'stats', 'stats': repository.get_chat_stats()})
        return result

    @app.get('/api/conversations')
    async def conversations(current=Depends(user)):
        return repository.conversations(current['id'])

    @app.post('/api/conversations', status_code=201)
    async def create_conversation(request: ConversationRequest, current=Depends(user)):
        try:
            result = repository.create_conversation(current['id'], request.peer_id, request.title)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        await manager.send_to_users({'type': 'conversations_changed'}, repository.member_ids(result['id']))
        return result

    @app.get('/api/conversations/{cid}')
    async def conversation(cid: str, current=Depends(user)):
        result = repository.conversation(cid, current['id'])
        if result is None:
            raise HTTPException(404, 'Диалог не найден.')
        return result

    @app.get('/api/conversations/{cid}/download')
    async def download_conversation(cid: str, format: str = Query('txt', pattern='^(txt|csv)$'), current=Depends(user)):
        conversation = repository.conversation(cid, current['id'])
        if not conversation:
            raise HTTPException(404, 'Диалог не найден.')
        rows = await asyncio.to_thread(repository.export_history, cid, current['id'])
        if format == 'txt':
            lines = [conversation['title'], 'Время сообщений указано в UTC.', '']
            for row in rows:
                lines.extend([f"[{row['created_at']}] {row['name']}", row['text'],
                              f"Тональность: {row['label']} · {row['confidence']:.1%}", ''])
            content = ('\ufeff' + '\n'.join(lines)).encode('utf-8')
            media = 'text/plain'
        else:
            stream = io.StringIO(newline='')
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['Дата (UTC)', 'Пользователь', 'Сообщение', 'Тональность', 'Уверенность', 'Негатив', 'Нейтрально', 'Позитив'])
            def safe_cell(value):
                return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
            for row in rows:
                writer.writerow([row['created_at'], safe_cell(row['name']), safe_cell(row['text']),
                                 row['label'], row['confidence'],
                                 *[row['scores'][k] for k in ('negative', 'neutral', 'positive')]])
            content = ('\ufeff' + stream.getvalue()).encode('utf-8')
            media = 'text/csv'

        filename = 'general' if cid == 'general' else 'dialog'
        return Response(content, media_type=media, headers={
            'Content-Disposition': f'attachment; filename="chat-{filename}.{format}"'})

    @app.post('/api/conversations/{cid}/read')
    async def mark_read(cid: str, request: ReadRequest, current=Depends(user)):
        if not repository.can_access(cid, current['id']):
            raise HTTPException(404, 'Диалог не найден.')
        repository.mark_read(cid, current['id'], request.last_id)
        await manager.broadcast({'type': 'conversations_changed'}, current['id'])
        return {'ok': True}

    @app.get('/api/stats')
    async def stats(current=Depends(user)):
        return repository.get_chat_stats() | {'online': manager.online}

    @app.get('/api/users')
    async def profiles(current=Depends(user)):
        return repository.profiles()

    @app.delete('/api/users/{uid}')
    async def delete_user(uid: str, request: DeleteUserRequest, current=Depends(user)):
        if not current.get('is_admin'):
            raise HTTPException(403, 'Удалять участников может только администратор.')
        if not request.confirmed:
            raise HTTPException(400, 'Подтвердите удаление участника.')
        if not rate_limit(delete_attempts, current['id'], 5, 60):
            raise HTTPException(429, 'Слишком много попыток удаления. Подождите минуту.')
        try:
            result = await asyncio.to_thread(repository.delete_user, current['id'], uid, request.password)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except (PermissionError, ValueError) as exc:
            raise HTTPException(403, str(exc))
        await manager.disconnect_user(uid)
        await manager.broadcast({'type': 'user_deleted', 'user_id': uid})
        await manager.broadcast({'type': 'presence', 'online': manager.online})
        await manager.broadcast({'type': 'stats', 'stats': repository.get_chat_stats()})
        return result

    @app.get('/api/users/{uid}')
    async def profile(uid: str, current=Depends(user)):
        result = repository.classify_user(uid)
        if result is None:
            raise HTTPException(404, 'Пользователь не найден.')
        return result

    @app.post('/api/analyze')
    async def analyze_message(request: AnalyzeRequest, current=Depends(user)):
        ensure_model()
        if not rate_limit(chat_attempts, current['id'], 30, 10):
            raise HTTPException(429, 'Слишком много сообщений. Подождите несколько секунд.')
        try:
            return await analyzer.analyze_message(validate_text(request.text))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/batches', status_code=202)
    async def process_batch(file: UploadFile = File(...), current=Depends(user)):
        ensure_model()
        try:
            content = await file.read(MAX_FILE_BYTES + 1)
            return await batch.process_batch(current['id'], file.filename or '', content)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            await file.close()

    @app.get('/api/batches')
    async def jobs(current=Depends(user)):
        return repository.jobs(current['id'])

    def owned_job(jid, current):
        job = repository.job(jid, current['id'])
        if not job:
            raise HTTPException(404, 'Задание не найдено.')
        return job

    @app.get('/api/batches/{jid}')
    async def job_detail(jid: str, current=Depends(user)):
        return owned_job(jid, current)

    @app.get('/api/batches/{jid}/stats')
    async def job_statistics(jid: str, current=Depends(user)):
        job = owned_job(jid, current)
        if job['status'] != 'done':
            raise HTTPException(409, 'Статистика появится после завершения обработки файла.')
        return {'job_id': job['id'], 'filename': job['filename'], 'created_at': job['created_at'],
                **repository.job_stats(jid)}

    @app.get('/api/batches/{jid}/results')
    async def job_results(jid: str, current=Depends(user)):
        job = owned_job(jid, current)
        if job['status'] != 'done':
            raise HTTPException(409, 'Результат ещё не готов.')
        return repository.job_results(jid)

    @app.get('/api/batches/{jid}/download')
    async def download(jid: str, format: str = Query('csv', pattern='^(csv|json)$'), current=Depends(user)):
        job = owned_job(jid, current)
        if job['status'] != 'done':
            raise HTTPException(409, 'Результат ещё не готов.')
        rows = repository.job_results(jid)
        if format == 'json':
            content = json.dumps(rows, ensure_ascii=False, indent=2).encode('utf-8')
            media = 'application/json'
        else:
            stream = io.StringIO(newline='')
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['text', 'label', 'confidence', 'negative', 'neutral', 'positive', 'truncated'])
            for row in rows:
                # апостроф предотвращает выполнение пользовательского текста как формулы в Excel
                text = row['text']
                if text.lstrip().startswith(('=', '+', '-', '@')):
                    text = "'" + text
                writer.writerow([text, row['label'], row['confidence'],
                                 *[row['scores'][k] for k in ('negative', 'neutral', 'positive')], row['truncated']])
            content = ('\ufeff' + stream.getvalue()).encode('utf-8')
            media = 'text/csv'
        return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="result-{jid}.{format}"'})

    @app.websocket('/ws')
    async def websocket_chat(ws: WebSocket):
        await ws.accept()
        cid = None
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            if len(raw) > 1024:
                await ws.close(code=1008)
                return
            first = json.loads(raw)
            current = repository.user_by_token(first.get('token', '')) if isinstance(first, dict) else None
            if not current:
                await ws.close(code=1008)
                return
            token = first['token']
            cid = manager.add(ws, current['id'])
            await manager.send(cid, {'type': 'welcome', 'user': current,
                                     'conversations': repository.conversations(current['id']),
                                     'stats': repository.get_chat_stats(), 'online': manager.online})
            await manager.broadcast({'type': 'presence', 'online': manager.online})
            while True:
                raw = await ws.receive_text()
                if not repository.user_by_token(token):
                    await ws.close(code=1008)
                    return
                if len(raw) > MAX_TEXT * 6 + 1024:
                    await manager.send(cid, {'type': 'error', 'message': 'Слишком большое сообщение.'})
                    continue
                try:
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError('Ожидается объект JSON.')
                    if payload.get('type') == 'ping':
                        await manager.send(cid, {'type': 'pong'})
                        continue
                    if payload.get('type') != 'message':
                        raise ValueError('Неизвестный тип запроса.')
                    if not rate_limit(chat_attempts, current['id'], 30, 10):
                        raise ValueError('Слишком много сообщений. Подождите несколько секунд.')
                    text = validate_text(payload.get('text'))
                    conversation_id = payload.get('conversation_id', 'general')
                    if not isinstance(conversation_id, str) or not repository.can_access(conversation_id, current['id']):
                        raise ValueError('Диалог не найден или недоступен.')
                    result = await analyzer.analyze_message(text)
                    if not repository.user_by_token(token):
                        return
                    message = repository.add_message(current['id'], text, result, conversation_id)
                    event = {'type': 'message', 'message': message}
                    if conversation_id == 'general':
                        await manager.broadcast(event)
                    else:
                        await manager.send_to_users(event, repository.member_ids(conversation_id))
                    await manager.broadcast({'type': 'stats', 'stats': repository.get_chat_stats()})
                except (ValueError, RuntimeError) as exc:
                    await manager.send(cid, {'type': 'error', 'message': str(exc)})
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except (json.JSONDecodeError, TypeError, AttributeError):
            await ws.close(code=1008)
        except Exception:
            log.exception('WebSocket failure')
        finally:
            if cid:
                await manager.remove(cid)
                await manager.broadcast({'type': 'presence', 'online': manager.online})

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app
