from pathlib import Path
import unittest
import test_api
from test_core import RESULT
from app.storage import Repository


class ClearChatTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    receive_type = staticmethod(test_api.ApiTests.receive_type)

    def clear(self, **changes):
        return self.client.post('/api/general/clear', headers=self.headers,
                                json={'password': 'password123', 'confirmed': True, **changes})

    def add(self, text='Сообщение', cid='general'):
        return self.app.state.repository.add_message(self.a['user']['id'], text, RESULT, cid)

    def test_requires_login_confirmation_and_password(self):
        original = self.add()
        response = self.client.post('/api/general/clear', json={'password': 'password123', 'confirmed': True})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.clear(confirmed=False).status_code, 400)
        self.assertEqual(self.clear(password='incorrect').status_code, 403)
        self.assertEqual(self.clear(password='').status_code, 422)
        self.assertEqual(self.app.state.repository.history(), [original])

    def test_preserves_direct_chats_files_accounts_and_recalculates_stats(self):
        repo = self.app.state.repository
        uid = self.a['user']['id']
        direct = repo.create_conversation(uid, self.b['user']['id'], 'Личная переписка')
        private = self.add('Личное сообщение', direct['id'])
        for i in range(5):
            self.add(f'Общее {i}')
        job = repo.create_job(uid, 'comments.txt', 1)
        repo.complete_job(job, ['Текст из файла'], [RESULT])
        response = self.clear()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['deleted'], 5)
        self.assertEqual(repo.history(), [])
        self.assertEqual(repo.history(conversation_id=direct['id']), [private])
        self.assertEqual(len(repo.job_results(job['id'])), 1)
        self.assertEqual(repo.job(job['id'], uid)['status'], 'done')
        self.assertEqual(repo.get_chat_stats()['total'], 1)
        self.assertEqual(repo.get_chat_stats()['batch_total'], 1)
        self.assertEqual(repo.get_chat_stats()['counts']['positive'], 1)
        self.assertEqual(repo.classify_user(uid)['total'], 2)
        self.assertEqual(repo.classify_user(uid)['category'], 'insufficient')
        self.assertEqual(repo.user_by_token(self.b['token']), self.b['user'])
        self.assertEqual(repo.user_by_token(self.a['token']), self.a['user'])
        self.assertEqual(len(list(Path(self.tmp.name).glob('**/*'))), 1)

    def test_clear_broadcast_and_sending_after_clear(self):
        with self.client.websocket_connect('/ws') as a, self.client.websocket_connect('/ws') as b:
            a.send_json({'token': self.a['token']}); self.receive_type(a, 'welcome')
            b.send_json({'token': self.b['token']}); self.receive_type(b, 'welcome')
            a.send_json({'type': 'message', 'text': 'Удаляемое сообщение'})
            old = self.receive_type(a, 'message')['message']
            self.receive_type(b, 'message')
            self.assertEqual(self.clear().status_code, 200)
            for ws in (a, b):
                event = self.receive_type(ws, 'history_cleared')
                self.assertEqual(event['conversation_id'], 'general')
                self.assertEqual(event['cleared_through_id'], old['id'])
                self.assertEqual(event['deleted'], 1)
                self.assertEqual(self.receive_type(ws, 'stats')['stats']['total'], 0)
            b.send_json({'type': 'message', 'text': 'Продолжаем общение'})
            new = self.receive_type(b, 'message')['message']
            self.assertGreater(new['id'], old['id'])
            self.assertEqual(self.receive_type(a, 'message')['message']['id'], new['id'])
            self.assertEqual(self.client.get('/api/history', headers=self.headers).json(), [new])

    def test_empty_chat_and_idempotence(self):
        self.assertEqual(self.clear().json()['deleted'], 0)
        old = self.add()
        self.assertEqual(self.clear().json()['cleared_through_id'], old['id'])
        self.assertEqual(self.clear().json()['deleted'], 0)
        self.assertGreater(self.add('Новое сообщение')['id'], old['id'])

    def test_deletion_persists_after_restarting_repository(self):
        self.add()
        self.clear()
        repo = Repository(Path(self.tmp.name) / 'chat.sqlite3')
        self.assertEqual(repo.history(), [])
        self.assertEqual(repo.user_by_token(self.a['token']), self.a['user'])

    def test_wrong_password_attempts_are_limited(self):
        self.add()
        for _ in range(5):
            self.assertEqual(self.clear(password='incorrect').status_code, 403)
        self.assertEqual(self.clear().status_code, 429)
        self.assertEqual(len(self.app.state.repository.history()), 1)


if __name__ == '__main__':
    unittest.main()
