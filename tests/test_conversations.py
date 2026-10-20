from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from test_core import RESULT
import test_api
from app.storage import Repository
from app.server import create_app
from fastapi.testclient import TestClient


class ConversationStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'chat.sqlite3'
        self.repo=Repository(self.path)
        self.a=self.repo.authenticate('Анна','password123',True)['user']['id']
        self.b=self.repo.authenticate('Борис','password123',True)['user']['id']
        self.c=self.repo.authenticate('Виктор','password123',True)['user']['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_thread_with_same_peer_has_separate_history(self):
        first=self.repo.create_conversation(self.a,self.b,'Учёба')
        self.repo.add_message(self.a,'Привет',RESULT,first['id'])
        second=self.repo.create_conversation(self.a,self.b,'Новая тема')
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.repo.history(conversation_id=second['id']),[])
        self.assertEqual(self.repo.history(conversation_id=first['id'])[0]['text'],'Привет')
        self.assertEqual(len(self.repo.conversations(self.b)),2)
        self.assertEqual(self.repo.conversations(self.c),[])

    def test_outsider_cannot_send_or_see_details(self):
        convo=self.repo.create_conversation(self.a,self.b)
        self.assertIsNone(self.repo.conversation(convo['id'],self.c))
        with self.assertRaises(ValueError):
            self.repo.add_message(self.c,'Чужое сообщение',RESULT,convo['id'])

    def test_continue_after_restart(self):
        convo=self.repo.create_conversation(self.a,self.b)
        original=self.repo.add_message(self.a,'Первое',RESULT,convo['id'])
        again=Repository(self.path)
        again.add_message(self.b,'Ответ',RESULT,convo['id'])
        history=again.history(conversation_id=convo['id'])
        self.assertEqual([m['text'] for m in history],['Первое','Ответ'])
        self.assertEqual(history[0]['id'],original['id'])

    def test_unread_is_independent_and_monotonic(self):
        c1=self.repo.create_conversation(self.a,self.b)
        c2=self.repo.create_conversation(self.a,self.b)
        m1=self.repo.add_message(self.a,'Один',RESULT,c1['id'])
        m2=self.repo.add_message(self.a,'Два',RESULT,c2['id'])
        self.assertEqual(self.repo.conversation(c1['id'],self.b)['unread'],1)
        self.repo.mark_read(c1['id'],self.b,m1['id'])
        self.repo.mark_read(c1['id'],self.b,0)
        self.assertEqual(self.repo.conversation(c1['id'],self.b)['unread'],0)
        self.assertEqual(self.repo.conversation(c2['id'],self.b)['unread'],1)

    def test_invalid_peer_and_title(self):
        for peer,title in ((self.a,''),('missing',''),(self.b,'a'*81),(self.b,'hello\nworld')):
            with self.assertRaises(ValueError):
                self.repo.create_conversation(self.a,peer,title)

    def test_migration_keeps_original_general_history(self):
        self.repo.add_message(self.a,'Старая переписка',RESULT)
        # проверяем миграцию базы первой версии, в которой ещё нет диалогов
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP INDEX ix_messages_conversation')
            db.execute('ALTER TABLE messages DROP COLUMN conversation_id')
            db.execute('DROP TABLE conversation_members')
            db.execute('DROP TABLE conversations')
        migrated=Repository(self.path)
        self.assertEqual(migrated.history()[0]['text'],'Старая переписка')
        self.assertEqual(migrated.history()[0]['conversation_id'],'general')
        self.assertEqual(migrated.conversations(self.a),[])
        self.assertEqual(migrated.get_chat_stats()['total'],1)
        Repository(self.path)
        self.assertEqual(len(migrated.history()),1)


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=TestClient(create_app(Path(self.tmp.name),test_api.StubAnalyzer()))
        self.client.__enter__()
        self.users=[self.client.post('/api/auth/register',json={'name':n,'password':'password123'}).json() for n in ('Анна','Борис','Виктор')]
        self.headers=[{'Authorization':'Bearer '+a['token']} for a in self.users]

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.tmp.cleanup()

    def create_conversation(self):
        r=self.client.post('/api/conversations',headers=self.headers[0],json={'peer_id':self.users[1]['user']['id'],'title':'Обсуждение проекта'})
        self.assertEqual(r.status_code,201)
        return r.json()['id']

    def test_welcome_opens_no_history(self):
        self.client.app.state.repository.add_message(self.users[0]['user']['id'],'Старый общий чат',RESULT)
        with self.client.websocket_connect('/ws') as ws:
            ws.send_json({'token':self.users[0]['token']})
            welcome=test_api.ApiTests.receive_type(ws,'welcome')
            self.assertNotIn('messages',welcome)
            self.assertEqual(welcome['conversations'],[])

    def test_private_endpoints_deny_outsider(self):
        cid=self.create_conversation()
        for path in (f'/api/conversations/{cid}',f'/api/history?conversation_id={cid}'):
            self.assertEqual(self.client.get(path,headers=self.headers[2]).status_code,404)
        self.assertEqual(self.client.post(f'/api/conversations/{cid}/read',headers=self.headers[2],json={'last_id':100}).status_code,404)
        self.assertEqual(self.client.get('/api/conversations',headers=self.headers[2]).json(),[])

    def test_private_delivery_and_outsider_injection(self):
        cid=self.create_conversation()
        with self.client.websocket_connect('/ws') as a,self.client.websocket_connect('/ws') as b,self.client.websocket_connect('/ws') as c:
            for ws,user in zip((a,b,c),self.users):
                ws.send_json({'token':user['token']});test_api.ApiTests.receive_type(ws,'welcome')
            a.send_json({'type':'message','text':'Личная переписка','conversation_id':cid})
            ma=test_api.ApiTests.receive_type(a,'message')['message'];mb=test_api.ApiTests.receive_type(b,'message')['message']
            self.assertEqual(ma['id'],mb['id'])
            c.send_json({'type':'ping'})
            while True:
                event=c.receive_json()
                self.assertNotEqual(event['type'],'message')
                if event['type']=='pong':break
            c.send_json({'type':'message','text':'Подмена','conversation_id':cid})
            self.assertEqual(test_api.ApiTests.receive_type(c,'error')['message'],'Диалог не найден или недоступен.')
            self.assertEqual(len(self.client.get(f'/api/history?conversation_id={cid}',headers=self.headers[0]).json()),1)

    def test_mark_read_and_independent_new_dialog(self):
        cid=self.create_conversation()
        repo=self.client.app.state.repository
        m=repo.add_message(self.users[0]['user']['id'],'Привет',RESULT,cid)
        self.assertEqual(self.client.get('/api/conversations',headers=self.headers[1]).json()[0]['unread'],1)
        self.client.post(f'/api/conversations/{cid}/read',headers=self.headers[1],json={'last_id':m['id']})
        self.assertEqual(self.client.get('/api/conversations',headers=self.headers[1]).json()[0]['unread'],0)
        new=self.create_conversation()
        self.assertNotEqual(cid,new)
        self.assertEqual(self.client.get(f'/api/history?conversation_id={new}',headers=self.headers[0]).json(),[])


if __name__=='__main__':unittest.main()
