import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from .config import MIN_PROFILE_MESSAGES, PROFILE_THRESHOLD


def now():
    return datetime.now(timezone.utc).isoformat()


def classify_counts(counts):
    total = sum(counts.values())
    shares = {key: round(counts.get(key, 0) / total, 4) if total else 0.0
              for key in ('negative', 'neutral', 'positive')}
    if total < MIN_PROFILE_MESSAGES:
        category = 'insufficient'
    elif counts.get('negative', 0) / total >= PROFILE_THRESHOLD:
        category = 'negative'
    elif counts.get('positive', 0) / total >= PROFILE_THRESHOLD:
        category = 'positive'
    elif counts.get('neutral', 0) / total >= PROFILE_THRESHOLD:
        category = 'neutral'
    else:
        category = 'mixed'
    return {'total': total, 'counts': counts, 'shares': shares, 'category': category}


class Repository:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
                  salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
                  text TEXT NOT NULL, label TEXT NOT NULL, confidence REAL NOT NULL,
                  scores TEXT NOT NULL, truncated INTEGER NOT NULL, created_at TEXT NOT NULL,
                  source TEXT NOT NULL, job_id TEXT);
                CREATE INDEX IF NOT EXISTS ix_messages_user ON messages(user_id);
                CREATE INDEX IF NOT EXISTS ix_messages_job ON messages(job_id);
                CREATE TABLE IF NOT EXISTS jobs(
                  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), filename TEXT NOT NULL,
                  status TEXT NOT NULL, total INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL, error TEXT);
            ''')
            if 'is_admin' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
            if 'deleted_at' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN deleted_at TEXT')
            # миграция сохраняет прежний общий чат и идентификаторы сообщений
            db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations(
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversation_members(
                  conversation_id TEXT NOT NULL REFERENCES conversations(id),
                  user_id TEXT NOT NULL REFERENCES users(id), last_read_id INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(conversation_id,user_id));
            ''')
            db.execute("INSERT OR IGNORE INTO conversations VALUES('general','group','Общий чат',?)", (now(),))
            columns = {r['name'] for r in db.execute('PRAGMA table_info(messages)')}
            if 'conversation_id' not in columns:
                db.execute('ALTER TABLE messages ADD COLUMN conversation_id TEXT REFERENCES conversations(id)')
            db.execute("UPDATE messages SET conversation_id='general' WHERE source='chat' AND conversation_id IS NULL")
            db.execute('CREATE INDEX IF NOT EXISTS ix_messages_conversation ON messages(conversation_id,id)')
            db.execute("UPDATE jobs SET status='failed', error='Сервер был остановлен. Загрузите файл повторно.' WHERE status IN ('queued','running')")
            db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _password(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 180_000).hex()

    def authenticate(self, name, password, register=False):
        name = name.strip()
        if not 2 <= len(name) <= 32 or any(ord(c) < 32 for c in name):
            raise ValueError('Имя должно содержать от 2 до 32 символов.')
        if not 6 <= len(password) <= 128:
            raise ValueError('Пароль должен содержать от 6 до 128 символов.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT * FROM users WHERE name_key=?', (name.casefold(),)).fetchone()
            if register:
                if user:
                    raise ValueError('Это имя уже занято. Используйте вход или другое имя.')
                salt = secrets.token_hex(16)
                uid = str(uuid4())
                try:
                    first_user = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
                    db.execute('INSERT INTO users(id,name,name_key,salt,password_hash,created_at,is_admin) VALUES(?,?,?,?,?,?,?)',
                               (uid, name, name.casefold(), salt, self._password(password, salt), now(), int(first_user)))
                except sqlite3.IntegrityError:
                    raise ValueError('Это имя уже занято.')
                user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            elif not user or not hmac.compare_digest(user['password_hash'], self._password(password, user['salt'])):
                raise ValueError('Неверное имя или пароль.')
            if user['deleted_at']:
                raise ValueError('Аккаунт удалён администратором. Вход закрыт.')
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), user['id'], time.time() + 7 * 86400))
            return {'token': token, 'user': {'id': user['id'], 'name': user['name'], 'is_admin': bool(user['is_admin'])}}

    def user_by_token(self, token):
        with self.connect() as db:
            row = db.execute('''SELECT u.id,u.name,u.is_admin FROM users u JOIN sessions s ON s.user_id=u.id
                             WHERE s.token_hash=? AND s.expires>? AND u.deleted_at IS NULL''',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def active_user(self, uid):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone() is not None

    def delete_user(self, actor_id, target_id, password):
        """закрывает доступ, сохраняя ссылки на автора в существующей переписке"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT * FROM users WHERE id=? AND deleted_at IS NULL', (actor_id,)).fetchone()
            if not actor or not actor['is_admin']:
                raise PermissionError('Удалять участников может только администратор.')
            if actor_id == target_id:
                raise PermissionError('Нельзя удалить свой аккаунт.')
            target = db.execute('SELECT * FROM users WHERE id=? AND deleted_at IS NULL', (target_id,)).fetchone()
            if not target:
                raise LookupError('Участник не найден или уже удалён.')
            if target['is_admin']:
                raise PermissionError('Нельзя удалить администратора.')
            if not hmac.compare_digest(actor['password_hash'], self._password(password, actor['salt'])):
                raise ValueError('Неверный пароль администратора. Участник не удалён.')
            db.execute('UPDATE users SET deleted_at=? WHERE id=?', (now(), target_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (target_id,))
            db.execute("UPDATE jobs SET status='failed',error='Аккаунт удалён администратором.' WHERE user_id=? AND status IN ('queued','running')", (target_id,))
            return {'user_id': target_id, 'deleted': True}

    def revoke(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def _message(row):
        result = dict(row)
        result['scores'] = json.loads(result['scores'])
        result['truncated'] = bool(result['truncated'])
        return result

    @staticmethod
    def _insert(db, uid, text, analysis, source, job_id=None, conversation_id=None):
        return db.execute('''INSERT INTO messages(user_id,text,label,confidence,scores,truncated,created_at,source,job_id,conversation_id)
                          VALUES(?,?,?,?,?,?,?,?,?,?)''',
                          (uid, text, analysis['label'], analysis['confidence'], json.dumps(analysis['scores']),
                           int(analysis['truncated']), now(), source, job_id, conversation_id)).lastrowid

    def add_message(self, uid, text, analysis, conversation_id='general'):
        if not self.can_access(conversation_id, uid):
            raise ValueError('Диалог не найден или недоступен.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone():
                raise ValueError('Аккаунт удалён. Отправка сообщений недоступна.')
            if db.execute('SELECT 1 FROM conversation_members m JOIN users u ON u.id=m.user_id WHERE m.conversation_id=? AND u.deleted_at IS NOT NULL', (conversation_id,)).fetchone():
                raise ValueError('Участник удалён. Этот диалог доступен только для просмотра.')
            mid = self._insert(db, uid, text, analysis, 'chat', conversation_id=conversation_id)
            row = db.execute("SELECT m.*,CASE WHEN u.deleted_at IS NULL THEN u.name ELSE u.name || ' (удалён)' END AS name FROM messages m JOIN users u ON u.id=m.user_id WHERE m.id=?", (mid,)).fetchone()
            return self._message(row)

    def clear_general(self, uid, password):
        """удаляет только общий чат, сохраняя рост идентификаторов сообщений"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT salt,password_hash,is_admin FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone()
            if not user or not user['is_admin']:
                raise PermissionError('Очищать общий чат может только администратор.')
            if not hmac.compare_digest(user['password_hash'], self._password(password, user['salt'])):
                raise ValueError('Неверный пароль. Переписка не удалена.')
            # граница удаления и DELETE выполняются в одной транзакции
            row = db.execute("SELECT COUNT(*) AS n,COALESCE(MAX(id),0) AS last_id FROM messages WHERE source='chat' AND conversation_id='general'").fetchone()
            db.execute("DELETE FROM messages WHERE source='chat' AND conversation_id='general' AND id<=?", (row['last_id'],))
            return {'conversation_id': 'general', 'deleted': row['n'],
                    'cleared_through_id': row['last_id']}

    def history(self, before=None, limit=100, conversation_id='general'):
        with self.connect() as db:
            rows = db.execute('''SELECT m.*,CASE WHEN u.deleted_at IS NULL THEN u.name ELSE u.name || ' (удалён)' END AS name FROM messages m JOIN users u ON u.id=m.user_id
                              WHERE source='chat' AND conversation_id=? AND m.id<? ORDER BY m.id DESC LIMIT ?''',
                              (conversation_id, before or 2**63 - 1, limit)).fetchall()
            return [self._message(row) for row in reversed(rows)]

    def export_history(self, cid, uid):
        if not self.can_access(cid, uid):
            raise PermissionError('Диалог не найден.')
        with self.connect() as db:
            return [self._message(row) for row in db.execute(
                "SELECT m.*,CASE WHEN u.deleted_at IS NULL THEN u.name ELSE u.name || ' (удалён)' END AS name FROM messages m JOIN users u ON u.id=m.user_id WHERE m.source='chat' AND m.conversation_id=? ORDER BY m.id", (cid,))]

    def can_access(self, cid, uid):
        with self.connect() as db:
            return db.execute('''SELECT 1 FROM conversations c WHERE c.id=? AND
                              EXISTS(SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL) AND
                              (c.kind='group' OR EXISTS(SELECT 1 FROM conversation_members m
                              WHERE m.conversation_id=c.id AND m.user_id=?))''', (cid, uid, uid)).fetchone() is not None

    def member_ids(self, cid):
        with self.connect() as db:
            return [r[0] for r in db.execute('SELECT user_id FROM conversation_members WHERE conversation_id=?', (cid,))]

    def create_conversation(self, uid, peer_id, title=''):
        if uid == peer_id:
            raise ValueError('Выберите другого пользователя.')
        title = title.strip()
        if len(title) > 80 or any(ord(c) < 32 for c in title):
            raise ValueError('Название — до 80 символов без переносов строки.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone():
                raise ValueError('Аккаунт удалён.')
            if not db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (peer_id,)).fetchone():
                raise ValueError('Пользователь не найден.')
            if not title:
                count = db.execute('''SELECT COUNT(*) FROM conversations c WHERE c.kind='direct'
                    AND EXISTS(SELECT 1 FROM conversation_members a WHERE a.conversation_id=c.id AND a.user_id=?)
                    AND EXISTS(SELECT 1 FROM conversation_members b WHERE b.conversation_id=c.id AND b.user_id=?)''',
                    (uid, peer_id)).fetchone()[0]
                title = f'Диалог {count + 1}'
            cid = str(uuid4())
            db.execute('INSERT INTO conversations VALUES(?,?,?,?)', (cid, 'direct', title, now()))
            db.executemany('INSERT INTO conversation_members(conversation_id,user_id) VALUES(?,?)', [(cid, uid), (cid, peer_id)])
        return self.conversation(cid, uid)

    def conversation(self, cid, uid):
        if not self.can_access(cid, uid):
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
            result = dict(row)
            result['participants'] = [dict(r) for r in db.execute('''SELECT u.id,CASE WHEN u.deleted_at IS NULL THEN u.name ELSE u.name || ' (удалён)' END AS name,u.deleted_at IS NOT NULL AS deleted FROM users u JOIN
                conversation_members m ON m.user_id=u.id WHERE m.conversation_id=? ORDER BY u.name_key''', (cid,))]
            result['read_only'] = any(p['deleted'] for p in result['participants'])
            latest = db.execute("SELECT id,text,created_at,user_id FROM messages WHERE conversation_id=? AND source='chat' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
            result['last_message'] = dict(latest) if latest else None
            result['message_count'] = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND source='chat'", (cid,)).fetchone()[0]
            result['unread'] = db.execute('''SELECT COUNT(*) FROM messages WHERE conversation_id=? AND source='chat'
                AND user_id<>? AND id>COALESCE((SELECT last_read_id FROM conversation_members
                WHERE conversation_id=? AND user_id=?),0)''', (cid, uid, cid, uid)).fetchone()[0] if result['kind']=='direct' else 0
            return result

    def conversations(self, uid):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('''SELECT c.id FROM conversations c JOIN conversation_members m
                ON m.conversation_id=c.id WHERE m.user_id=? ORDER BY c.created_at DESC,c.id DESC''', (uid,))]
        results = [result for cid in ids if (result := self.conversation(cid, uid)) is not None]
        return sorted(results, key=lambda c: (c['last_message'] or {}).get('created_at', c['created_at']), reverse=True)

    def mark_read(self, cid, uid, last_id):
        with self.connect() as db:
            # отметка прочтения ограничена существующими сообщениями этого диалога
            actual = db.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE conversation_id=? AND id<=?', (cid, last_id)).fetchone()[0]
            db.execute('UPDATE conversation_members SET last_read_id=MAX(last_read_id,?) WHERE conversation_id=? AND user_id=?', (actual, cid, uid))

    def get_chat_stats(self):
        with self.connect() as db:
            counts = {k: 0 for k in ('negative', 'neutral', 'positive')}
            for row in db.execute("SELECT label,COUNT(*) AS n FROM messages WHERE source='chat' GROUP BY label"):
                counts[row['label']] = row['n']
            result = classify_counts(counts)
            result.pop('category')
            result['users'] = db.execute('SELECT COUNT(*) FROM users WHERE deleted_at IS NULL').fetchone()[0]
            result['batch_total'] = db.execute("SELECT COUNT(*) FROM messages WHERE source='batch'").fetchone()[0]
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=29)
            result['timeline_start'] = start.isoformat()
            result['timeline_end'] = end.isoformat()
            result['timeline'] = [dict(row) for row in db.execute('''SELECT substr(created_at,1,10) AS day,label,COUNT(*) AS n
                                 FROM messages WHERE source='chat' AND substr(created_at,1,10) BETWEEN ? AND ?
                                 GROUP BY day,label ORDER BY day,label''', (start.isoformat(), end.isoformat()))]
            return result

    def classify_user(self, uid):
        with self.connect() as db:
            user = db.execute('SELECT id,name,created_at,is_admin FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone()
            if not user:
                return None
            counts = {k: 0 for k in ('negative', 'neutral', 'positive')}
            for row in db.execute('SELECT label,COUNT(*) AS n FROM messages WHERE user_id=? GROUP BY label', (uid,)):
                counts[row['label']] = row['n']
            result = dict(user) | classify_counts(counts)
            result['chat_total'] = db.execute("SELECT COUNT(*) FROM messages WHERE user_id=? AND source='chat'", (uid,)).fetchone()[0]
            result['batch_total'] = result['total'] - result['chat_total']
            return result

    def profiles(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM users WHERE deleted_at IS NULL ORDER BY name_key')]
        return [profile for uid in ids if (profile := self.classify_user(uid)) is not None]

    def create_job(self, uid, filename, total):
        jid = str(uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone():
                raise ValueError('Аккаунт удалён.')
            db.execute('INSERT INTO jobs(id,user_id,filename,status,total,created_at) VALUES(?,?,?,?,?,?)',
                       (jid, uid, filename, 'queued', total, now()))
        return self.job(jid, uid)

    def job(self, jid, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND user_id=?', (jid, uid)).fetchone()
            return dict(row) if row else None

    def jobs(self, uid):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50', (uid,))]

    def update_job(self, jid, status, processed=0, error=None):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,processed=?,error=? WHERE id=? AND user_id IN (SELECT id FROM users WHERE deleted_at IS NULL)', (status, processed, error, jid))

    def complete_job(self, job, texts, analyses):
        # результаты всего файла сохраняются атомарно и только после полного анализа
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (job['user_id'],)).fetchone():
                raise ValueError('Аккаунт удалён. Результаты задания не сохраняются.')
            for text, analysis in zip(texts, analyses):
                self._insert(db, job['user_id'], text, analysis, 'batch', job['id'])
            db.execute("UPDATE jobs SET status='done',processed=total WHERE id=?", (job['id'],))

    def job_stats(self, jid):
        with self.connect() as db:
            counts = {key: 0 for key in ('positive', 'neutral', 'negative')}
            for row in db.execute("SELECT label,COUNT(*) AS n FROM messages WHERE job_id=? AND source='batch' GROUP BY label", (jid,)):
                counts[row['label']] = row['n']
            total = sum(counts.values())
            return {'total': total, 'counts': counts,
                    'shares': {key: round(value / total, 4) if total else 0.0 for key, value in counts.items()}}

    def job_results(self, jid):
        with self.connect() as db:
            return [self._message(r) for r in db.execute('SELECT * FROM messages WHERE job_id=? ORDER BY id', (jid,))]
