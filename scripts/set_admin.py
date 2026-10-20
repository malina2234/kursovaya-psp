import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent.parent


def set_admin(path, name, remove=False):
    if not path.is_file():
        raise ValueError('База не найдена. Сначала запустите программу и создайте аккаунт.')
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if 'is_admin' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
        if 'deleted_at' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN deleted_at TEXT')
        user=db.execute('SELECT id,is_admin FROM users WHERE name_key=? AND deleted_at IS NULL', (name.strip().casefold(),)).fetchone()
        if user is None:
            raise ValueError('Аккаунт не найден. Проверьте имя пользователя.')
        if remove and user[1] and db.execute('SELECT COUNT(*) FROM users WHERE is_admin=1 AND deleted_at IS NULL').fetchone()[0]<=1:
            raise ValueError('Нельзя снять права последнего администратора.')
        db.execute('UPDATE users SET is_admin=? WHERE id=?', (int(not remove),user[0]))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name',help='Имя существующего аккаунта')
    parser.add_argument('--remove',action='store_true',help='Снять права администратора')
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    args=parser.parse_args()
    try:
        set_admin(args.data_dir/'chat.sqlite3',args.name,args.remove)
        print('Роль сохранена. Обновите страницу приложения для отображения изменений.')
    except (ValueError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
