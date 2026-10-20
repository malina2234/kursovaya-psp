"""проверяет HTTPS и WSS на отдельной тестовой базе и добавляет тестовые сообщения"""
import argparse
import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
from websockets.asyncio.client import connect

async def event(ws,kind):
    async with asyncio.timeout(20):
        while True:
            value=json.loads(await ws.recv())
            if value['type']==kind:return value

async def run(local,report):
    records=[]
    def check(name,ok):
        records.append({'name':name,'passed':bool(ok)})
        assert ok,name
    async with httpx.AsyncClient(base_url=local,timeout=30,trust_env=False) as client:
        accounts=[]
        for name in ('Александр','Мария'):
            r=await client.post('/api/auth/login',json={'name':name,'password':'demo2026'})
            r.raise_for_status();accounts.append(r.json())
        headers={'Authorization':'Bearer '+accounts[0]['token']}
        for _ in range(45):
            info=(await client.get('/api/access',headers=headers)).json()
            if info['internet']['status'] in ('online','error'):break
            await asyncio.sleep(2)
        check('Интернет-туннель готов',info['internet']['status']=='online')
        public=info['internet']['url']
        check('Одновременно включён режим Wi-Fi',info['lan_enabled'] and bool(info['lan_urls']))
        remote_health=(await client.get(public+'/api/health')).json()
        local_health=(await client.get('/api/health')).json()
        check('Локальный и интернет-адрес ведут на один сервер',remote_health['instance_id']==local_health['instance_id'])
        html=await client.get(public+'/')
        check('Через HTTPS открыт новый интерфейс','Мои диалоги' in html.text and 'show-access' in html.text)
        other={'Authorization':'Bearer '+accounts[1]['token']}
        r=await client.post(public+'/api/conversations',headers=headers,json={'peer_id':accounts[1]['user']['id'],'title':'Проверка интернет-доступа'})
        r.raise_for_status();cid=r.json()['id']
        text='Проверка связи '+uuid4().hex[:8]
        async with connect(local.replace('http://','ws://')+'/ws',proxy=None) as a,connect(public.replace('https://','wss://')+'/ws',proxy=None) as b:
            await a.send(json.dumps({'token':accounts[0]['token']}));await event(a,'welcome')
            await b.send(json.dumps({'token':accounts[1]['token']}));await event(b,'welcome')
            await a.send(json.dumps({'type':'message','conversation_id':cid,'text':text}))
            m=(await event(b,'message'))['message']
            check('Сообщение из Wi-Fi приходит через WSS',m['text']==text and m['conversation_id']==cid)
            await b.send(json.dumps({'type':'message','conversation_id':cid,'text':'Ответ через интернет'}))
            m=await event(a,'message')
            if m['message']['text']==text:m=await event(a,'message')
            check('Ответ через WSS приходит локальному клиенту',m['message']['text']=='Ответ через интернет')
        remote_history=(await client.get(public+'/api/history',params={'conversation_id':cid},headers=other)).json()
        check('Оба способа подключения сохраняют одну историю',len(remote_history)==2)
        report.write_text(json.dumps({'public_url':public,'local_url':local,'checks':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'Passed {len(records)} checks. Public URL: {public}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-url',default='http://127.0.0.1:8020')
    parser.add_argument('--report',type=Path,default=Path('internet_e2e_report.json'))
    args=parser.parse_args();asyncio.run(run(args.local_url,args.report))
