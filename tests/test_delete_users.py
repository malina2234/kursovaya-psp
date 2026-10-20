import asyncio
from pathlib import Path
import threading
import time
import unittest
from starlette.websockets import WebSocketDisconnect
from app.storage import Repository
from scripts.set_admin import set_admin
import test_api
from test_core import RESULT


class DeleteUsersTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    receive_type = staticmethod(test_api.ApiTests.receive_type)

    def delete(self, uid=None, headers=None, **changes):
        uid=uid or self.b['user']['id']
        return self.client.request('DELETE',f'/api/users/{uid}',headers=headers or self.headers,
                                   json={'password':'password123','confirmed':True,**changes})

    def test_requires_admin_confirmation_and_password(self):
        uid=self.b['user']['id']
        url=f'/api/users/{uid}'
        self.assertEqual(self.client.request('DELETE',url,json={'password':'password123','confirmed':True}).status_code,401)
        member={'Authorization':'Bearer '+self.b['token']}
        self.assertEqual(self.delete(self.a['user']['id'],headers=member).status_code,403)
        self.assertEqual(self.delete(confirmed=False).status_code,400)
        self.assertEqual(self.delete(password='incorrect').status_code,403)
        self.assertEqual(self.delete(password='').status_code,422)
        self.assertIsNotNone(self.app.state.repository.user_by_token(self.b['token']))

    def test_cannot_delete_self_or_another_admin(self):
        self.assertEqual(self.delete(self.a['user']['id']).status_code,403)
        set_admin(Path(self.tmp.name)/'chat.sqlite3','Борис')
        self.assertEqual(self.delete().status_code,403)
        self.assertEqual(len(self.app.state.repository.profiles()),2)

    def test_deleted_account_disappears_and_all_tokens_are_revoked(self):
        repo=self.app.state.repository
        extra=repo.authenticate('Борис','password123')['token']
        self.assertEqual(self.delete().status_code,200)
        self.assertIsNone(repo.user_by_token(extra))
        self.assertIsNone(repo.user_by_token(self.b['token']))
        self.assertEqual([u['id'] for u in repo.profiles()],[self.a['user']['id']])
        self.assertEqual(repo.get_chat_stats()['users'],1)
        self.assertEqual(self.client.get('/api/users/'+self.b['user']['id'],headers=self.headers).status_code,404)
        self.assertEqual(self.client.get('/api/me',headers={'Authorization':'Bearer '+extra}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'name':'Борис','password':'password123'}).status_code,400)
        self.assertEqual(self.client.post('/api/auth/register',json={'name':'БОРИС','password':'password123'}).status_code,400)
        self.assertEqual(self.delete().status_code,404)
        self.assertEqual(self.delete('missing').status_code,404)
        with self.assertRaises(ValueError):set_admin(Path(self.tmp.name)/'chat.sqlite3','Борис')
        again=Repository(Path(self.tmp.name)/'chat.sqlite3')
        self.assertFalse(again.active_user(self.b['user']['id']))
        self.assertIsNone(again.user_by_token(extra))

    def test_messages_preserved_and_private_chat_becomes_read_only(self):
        repo=self.app.state.repository
        a,b=self.a['user']['id'],self.b['user']['id']
        cid=repo.create_conversation(a,b,'Учёба')['id']
        repo.add_message(b,'Старое общее сообщение',RESULT)
        repo.add_message(a,'Личное сообщение',RESULT,cid)
        repo.add_message(b,'Ответ участника',RESULT,cid)
        job=repo.create_job(b,'comments.txt',1)
        repo.complete_job(job,['Готовый результат'],[RESULT])
        self.assertEqual(self.delete().status_code,200)
        history=repo.history()
        self.assertEqual(history[0]['text'],'Старое общее сообщение')
        self.assertEqual(history[0]['name'],'Борис (удалён)')
        conversation=repo.conversation(cid,a)
        self.assertTrue(conversation['read_only'])
        self.assertEqual(len(repo.history(conversation_id=cid)),2)
        self.assertEqual(len(repo.job_results(job['id'])),1)
        self.assertEqual(repo.get_chat_stats()['total'],3)
        self.assertEqual(repo.get_chat_stats()['batch_total'],1)
        self.assertTrue(next(p for p in conversation['participants'] if p['id']==b)['deleted'])
        self.assertIn('Борис (удалён)',self.client.get(f'/api/conversations/{cid}/download',headers=self.headers).text)
        with self.assertRaises(ValueError):repo.add_message(a,'Нельзя отправить',RESULT,cid)
        with self.assertRaises(ValueError):repo.add_message(b,'Нельзя отправить',RESULT)
        with self.assertRaises(ValueError):repo.create_conversation(a,b)
        with self.assertRaises(ValueError):repo.create_conversation(b,a)
        self.assertFalse(repo.can_access('general',b))
        repo.add_message(a,'Общий чат работает',RESULT)

    def test_disconnects_all_tabs_and_notifies_other_participants(self):
        with self.client.websocket_connect('/ws') as admin, self.client.websocket_connect('/ws') as tab1, self.client.websocket_connect('/ws') as tab2:
            admin.send_json({'token':self.a['token']});self.receive_type(admin,'welcome')
            for ws in (tab1,tab2):
                ws.send_json({'token':self.b['token']});self.receive_type(ws,'welcome')
            self.assertEqual(self.delete().status_code,200)
            event=self.receive_type(admin,'user_deleted')
            self.assertEqual(event['user_id'],self.b['user']['id'])
            self.assertEqual(self.receive_type(admin,'presence')['online'],1)
            for ws in (tab1,tab2):
                with self.assertRaises(WebSocketDisconnect) as closed:
                    for _ in range(10):ws.receive_json()
                self.assertEqual(closed.exception.code,1008)
            admin.send_json({'type':'message','text':'Администратор остаётся в сети'})
            self.assertEqual(self.receive_type(admin,'message')['message']['text'],'Администратор остаётся в сети')
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/ws') as deleted:
                deleted.send_json({'token':self.b['token']});deleted.receive_json()

    def test_running_and_queued_batches_do_not_save_after_deletion(self):
        started=threading.Event();release=threading.Event()
        original=self.app.state.analyzer.analyze_many
        async def delayed(texts):
            started.set()
            while not release.is_set():await asyncio.sleep(.005)
            return [dict(RESULT) for _ in texts]
        self.app.state.analyzer.analyze_many=delayed
        member={'Authorization':'Bearer '+self.b['token']}
        try:
            first=self.client.post('/api/batches',headers=member,files={'file':('a.txt','a\nb')}).json()
            self.assertTrue(started.wait(2))
            second=self.client.post('/api/batches',headers=member,files={'file':('b.txt','c')}).json()
            self.assertEqual(self.delete().status_code,200)
            release.set()
            # новое задание проходит через ту же очередь после удаления владельца прежних заданий
            third=self.client.post('/api/batches',headers=self.headers,files={'file':('c.txt','d')}).json()
            repo=self.app.state.repository
            for _ in range(100):
                if repo.job(third['id'],self.a['user']['id'])['status']=='done':break
                time.sleep(.01)
            self.assertEqual(repo.job(third['id'],self.a['user']['id'])['status'],'done')
            for j in (first,second):
                self.assertEqual(repo.job(j['id'],self.b['user']['id'])['status'],'failed')
                self.assertEqual(repo.job_results(j['id']),[])
            self.assertEqual(repo.get_chat_stats()['batch_total'],1)
        finally:
            release.set();self.app.state.analyzer.analyze_many=original

    def test_password_attempts_are_limited(self):
        for _ in range(5):self.assertEqual(self.delete(password='incorrect').status_code,403)
        self.assertEqual(self.delete().status_code,429)
        self.assertTrue(self.app.state.repository.active_user(self.b['user']['id']))


if __name__=='__main__':unittest.main()
