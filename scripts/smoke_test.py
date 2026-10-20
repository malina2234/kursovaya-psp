"""проверяет сервер с настоящей моделью; запускать на отдельной базе, поскольку создаются тестовые записи"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
import httpx
from websockets.asyncio.client import connect


async def event(ws, kind, timeout=15):
    async with asyncio.timeout(timeout):
        while True:
            data = json.loads(await ws.recv())
            if data['type'] == kind:
                return data


async def run(base, report):
    records = []
    def check(name, condition, details=None):
        records.append({'name':name, 'passed':bool(condition), 'details':details})
        if not condition:
            raise AssertionError(f'{name}: {details}')
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        check('Модель доступна', (await client.get('/api/health')).json()['model_ready'])
        accounts = []
        for name in ('Александр', 'Мария'):
            body = {'name':name, 'password':'demo2026'}
            r = await client.post('/api/auth/register', json=body)
            if r.status_code != 200:
                r = await client.post('/api/auth/login', json=body)
            r.raise_for_status()
            accounts.append(r.json())
        headers = {'Authorization':'Bearer '+accounts[0]['token']}
        samples = [('Спасибо за помощь! Всё отлично, я очень доволен.', 'positive'),
                   ('Ужасный сервис, всё сломано, я недоволен.', 'negative'),
                   ('Встреча состоится завтра в десять часов.', 'neutral')]
        outputs = []
        for text, expected in samples:
            r = await client.post('/api/analyze', headers=headers, json={'text':text})
            r.raise_for_status()
            result = r.json()
            outputs.append({'text':text, 'expected':expected, 'actual':result['label'], 'confidence':result['confidence']})
        check('Реальная модель: три класса', all(r['actual']==r['expected'] for r in outputs), outputs)
        wsbase = base.replace('http://','ws://').replace('https://','wss://')
        async with connect(wsbase+'/ws') as a, connect(wsbase+'/ws') as b:
            await a.send(json.dumps({'token':accounts[0]['token']})); await event(a,'welcome')
            await b.send(json.dumps({'token':accounts[1]['token']})); await event(b,'welcome')
            await a.send(json.dumps({'type':'message','text':samples[0][0]}))
            received_a, received_b = await asyncio.gather(event(a,'message'), event(b,'message'))
            check('Маршрутизация двум клиентам', received_a['message']['id']==received_b['message']['id'])
            await b.send(json.dumps({'type':'message','text':samples[1][0]}))
            await asyncio.gather(event(a,'message'), event(b,'message'))
            await a.send(json.dumps({'type':'message','text':samples[2][0]}))
            await asyncio.gather(event(a,'message'), event(b,'message'))
            await a.send(json.dumps({'type':'message','text':'<script>alert(1)</script>'}))
            html_message = (await event(a,'message'))['message']
            await event(b,'message')
            check('HTML хранится как текст', html_message['text']=='<script>alert(1)</script>')
            comments = [text for text,_ in samples] * 100
            payload = '\n'.join(comments).encode('utf-8')
            upload = await client.post('/api/batches', headers=headers, files={'file':('demo_300.txt',payload)})
            upload.raise_for_status()
            jid = upload.json()['id']
            began = time.perf_counter()
            await b.send(json.dumps({'type':'message','text':'Отличная идея! Буду рад продолжить работу.'}))
            live = await event(b,'message')
            elapsed = time.perf_counter()-began
            check('Чат отвечает во время пакетного анализа', elapsed<10, {'response_seconds':round(elapsed,3),'message_id':live['message']['id']})
            for _ in range(300):
                job = (await client.get(f'/api/batches/{jid}', headers=headers)).json()
                if job['status'] in ('done','failed'):
                    break
                await asyncio.sleep(.1)
            check('Пакет из 300 комментариев завершён', job['status']=='done', job['status'])
            results = (await client.get(f'/api/batches/{jid}/results',headers=headers)).json()
            check('Все строки размечены', len(results)==300)
            r = await client.get(f'/api/batches/{jid}/download', headers=headers)
            check('CSV экспорт', r.status_code==200 and r.content.startswith(b'\xef\xbb\xbf'))
            other = {'Authorization':'Bearer '+accounts[1]['token']}
            check('Чужой результат недоступен', (await client.get(f'/api/batches/{jid}/results',headers=other)).status_code==404)
            await a.send(json.dumps({'type':'message','text':' '}))
            check('Пустое сообщение отклоняется', (await event(a,'error'))['message']=='Сообщение не должно быть пустым.')
        check('Битый JSON-файл отклоняется', (await client.post('/api/batches',headers=headers,files={'file':('bad.json',b'[null]')})).status_code==400)
        profile = (await client.get('/api/users/'+accounts[0]['user']['id'],headers=headers)).json()
        check('Профиль учитывает пакет', profile['batch_total']>=300 and profile['category']=='mixed',profile['category'])
        stats = (await client.get('/api/stats',headers=headers)).json()
        check('Статистика отделяет чат от пакетов',stats['total']>=5 and stats['batch_total']>=300,stats)
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'base_url':base,
                                 'model':'cointegrated/rubert-tiny-sentiment-balanced (ONNX FP32)',
                                 'checks':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Passed {len(records)} checks. Report: {report}')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--report',type=Path,default=Path('docs/smoke_report.json'))
    args=parser.parse_args()
    asyncio.run(run(args.base_url,args.report))
