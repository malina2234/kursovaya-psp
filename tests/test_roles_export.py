import csv
import io
from pathlib import Path
import sqlite3
from contextlib import closing
import unittest
import test_api
from test_core import RESULT
from scripts.set_admin import set_admin


class RolesExportTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown

    def test_first_account_is_admin_and_client_cannot_choose_role(self):
        self.assertTrue(self.a['user']['is_admin'])
        self.assertFalse(self.b['user']['is_admin'])
        account=self.client.post('/api/auth/register',json={'name':'Виктор','password':'password123','is_admin':True}).json()
        self.assertFalse(account['user']['is_admin'])

    def test_member_cannot_clear_even_with_correct_password(self):
        repo=self.app.state.repository
        repo.add_message(self.a['user']['id'],'Сохраняем',RESULT)
        headers={'Authorization':'Bearer '+self.b['token']}
        response=self.client.post('/api/general/clear',headers=headers,json={'password':'password123','confirmed':True})
        self.assertEqual(response.status_code,403)
        self.assertEqual(len(repo.history()),1)
        with self.assertRaises(PermissionError):
            repo.clear_general(self.b['user']['id'],'password123')

    def test_local_role_change_is_applied_to_existing_sessions(self):
        path=Path(self.tmp.name)/'chat.sqlite3'
        with self.assertRaises(ValueError):set_admin(path,'Анна',remove=True)
        set_admin(path,'Борис')
        set_admin(path,'Анна',remove=True)
        self.assertFalse(self.client.get('/api/me',headers=self.headers).json()['is_admin'])
        response=self.client.post('/api/general/clear',headers=self.headers,json={'password':'password123','confirmed':True})
        self.assertEqual(response.status_code,403)
        headers={'Authorization':'Bearer '+self.b['token']}
        self.assertTrue(self.client.get('/api/me',headers=headers).json()['is_admin'])
        self.assertEqual(self.client.post('/api/general/clear',headers=headers,json={'password':'password123','confirmed':True}).status_code,200)
        with self.assertRaises(ValueError):set_admin(path,'Неизвестный')

    def test_export_includes_all_messages_with_csv_escaping(self):
        repo=self.app.state.repository
        for i in range(104):repo.add_message(self.a['user']['id'],f'Сообщение {i}',RESULT)
        special='=HYPERLINK("test"); строка\nновая строка'
        repo.add_message(self.b['user']['id'],special,RESULT)
        self.assertEqual(len(self.client.get('/api/history',headers=self.headers).json()),100)
        response=self.client.get('/api/conversations/general/download?format=csv',headers=self.headers)
        self.assertEqual(response.status_code,200)
        rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),106)
        self.assertEqual(rows[1][2],'Сообщение 0')
        self.assertEqual(rows[-1][2],"'"+special)
        self.assertIn('attachment;',response.headers['content-disposition'])
        text=self.client.get('/api/conversations/general/download?format=txt',headers=self.headers)
        self.assertIn('Сообщение 0',text.text)
        self.assertIn('Сообщение 103',text.text)
        self.assertIn(special,text.text)

    def test_private_export_owner_and_outsider(self):
        repo=self.app.state.repository
        third=repo.authenticate('Виктор','password123',True)
        cid=repo.create_conversation(self.a['user']['id'],self.b['user']['id'])['id']
        repo.add_message(self.a['user']['id'],'Личное сообщение',RESULT,cid)
        response=self.client.get(f'/api/conversations/{cid}/download?format=txt',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertIn('Личное сообщение',response.text)
        outsider={'Authorization':'Bearer '+third['token']}
        for format in ('txt','csv'):
            self.assertEqual(self.client.get(f'/api/conversations/{cid}/download?format={format}',headers=outsider).status_code,404)
        self.assertEqual(self.client.get(f'/api/conversations/{cid}/download').status_code,401)
        self.assertEqual(self.client.get(f'/api/conversations/{cid}/download?format=html',headers=self.headers).status_code,422)

    def test_migration_never_grants_existing_accounts_admin_implicitly(self):
        path=Path(self.tmp.name)/'chat.sqlite3'
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('ALTER TABLE users DROP COLUMN is_admin')
        set_admin(path,'Борис')
        self.assertTrue(self.app.state.repository.user_by_token(self.b['token'])['is_admin'])
        self.assertFalse(self.app.state.repository.user_by_token(self.a['token'])['is_admin'])


if __name__=='__main__':unittest.main()
