"""заполняет отдельную демонстрационную базу; повторный запуск сохраняет существующие данные"""
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT
from app.sentiment import SentimentAnalyzer
from app.storage import Repository

MESSAGES = {
    'Александр': ['Спасибо за помощь! Всё отлично, я очень доволен.',
                  'Отличная работа, большое спасибо команде!',
                  'Мне очень понравился результат, просто замечательно!',
                  'Очень удобно и понятно. Молодцы!',
                  'Прекрасная идея, с удовольствием поддержу!'],
    'Мария': ['Ужасный сервис, всё сломано, я недовольна.',
              'Плохое качество, я очень разочарована.',
              'Работает ужасно, постоянно возникают ошибки.',
              'Мне совершенно не понравилось, кошмар.',
              'Отвратительный результат, жаль потраченного времени.'],
    'Дмитрий': ['Встреча состоится завтра в десять часов.',
                'Сервер запущен на порту 8000.',
                'Файл содержит десять строк.',
                'Следующее занятие в четверг.',
                'Список участников находится в таблице.'],
}

async def create():
    path = ROOT / 'demo_data' / 'chat.sqlite3'
    if path.exists():
        print('Demo database already exists. No changes made.')
        return
    analyzer=SentimentAnalyzer()
    analyzer.load()
    if not analyzer.ready:
        raise RuntimeError(analyzer.error)
    try:
        repo=Repository(path)
        users={name:repo.authenticate(name,'demo2026',True)['user']['id'] for name in MESSAGES}
        for index in range(5):
            for name,texts in MESSAGES.items():
                text=texts[index]
                result=await analyzer.analyze_message(text)
                repo.add_message(users[name],text,result)
        uid=users['Александр']
        texts=(ROOT/'samples'/'comments.txt').read_text(encoding='utf-8').splitlines()
        job=repo.create_job(uid,'comments.txt',len(texts))
        repo.complete_job(job,texts,await analyzer.analyze_many(texts))
        print('Demo ready: 3 users, 15 chat messages, 9 file comments. Password: demo2026')
    finally:
        analyzer.close()

if __name__=='__main__':
    asyncio.run(create())
