import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from fastapi.testclient import TestClient
from app.server import create_app
from test_core import RESULT


class StubAnalyzer:
    ready = True
    error = None
    def load(self): pass
    def close(self): pass
    async def analyze_message(self, text):
        return dict(RESULT)
    async def analyze_many(self, texts):
        await asyncio.sleep(.005)
        return [dict(RESULT) for _ in texts]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name), StubAnalyzer())
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.a = self.client.post('/api/auth/register', json={'name':'Анна','password':'password123'}).json()
        self.b = self.client.post('/api/auth/register', json={'name':'Борис','password':'password123'}).json()
        self.headers = {'Authorization': 'Bearer '+self.a['token']}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.tmp.cleanup()

    @staticmethod
    def receive_type(ws, kind):
        for _ in range(15):
            result = ws.receive_json()
            if result['type'] == kind:
                return result
        raise AssertionError(f'No {kind} event')

    def test_auth_required(self):
        self.assertEqual(self.client.get('/api/stats').status_code,401)
        self.assertEqual(self.client.get('/api/health').status_code,200)
        self.assertEqual(self.client.post('/api/analyze',headers=self.headers,json={'text':'  '}).status_code,400)

    def test_two_clients_receive_message(self):
        with self.client.websocket_connect('/ws') as a, self.client.websocket_connect('/ws') as b:
            a.send_json({'token':self.a['token']}); self.receive_type(a,'welcome')
            b.send_json({'token':self.b['token']}); self.receive_type(b,'welcome')
            a.send_json({'type':'message','text':'Привет всем!','user_id':self.b['user']['id']})
            received_a = self.receive_type(a,'message')['message']
            received_b = self.receive_type(b,'message')['message']
            self.assertEqual(received_a['id'],received_b['id'])
            self.assertEqual(received_b['user_id'],self.a['user']['id'])
            self.assertEqual(self.client.get('/api/stats',headers=self.headers).json()['total'],1)

    def test_invalid_websocket_message(self):
        with self.client.websocket_connect('/ws') as ws:
            ws.send_json({'token':self.a['token']}); self.receive_type(ws,'welcome')
            ws.send_json({'type':'message','text':' '})
            self.assertEqual(self.receive_type(ws,'error')['message'],'Сообщение не должно быть пустым.')
            ws.send_json({'type':'message','text':'ok'})
            self.assertEqual(self.receive_type(ws,'message')['message']['text'],'ok')

    def test_batch_results_and_ownership(self):
        response = self.client.post('/api/batches',headers=self.headers,files={'file':('a.txt','a\nb\nc')})
        self.assertEqual(response.status_code,202)
        jid=response.json()['id']
        for _ in range(100):
            status=self.client.get(f'/api/batches/{jid}',headers=self.headers).json()['status']
            if status=='done':break
            time.sleep(.01)
        self.assertEqual(status,'done')
        self.assertEqual(len(self.client.get(f'/api/batches/{jid}/results',headers=self.headers).json()),3)
        self.assertIn('positive', self.client.get(f'/api/batches/{jid}/download',headers=self.headers).text)
        other={'Authorization':'Bearer '+self.b['token']}
        self.assertEqual(self.client.get(f'/api/batches/{jid}/results',headers=other).status_code,404)
        self.assertEqual(self.client.get('/api/stats',headers=self.headers).json()['total'],0)

    def test_batch_validation(self):
        r=self.client.post('/api/batches',headers=self.headers,files={'file':('a.json','[null]')})
        self.assertEqual(r.status_code,400)
        self.assertEqual(len(self.client.get('/api/batches',headers=self.headers).json()),0)

    def test_single_analysis_does_not_change_history(self):
        response=self.client.post('/api/analyze',headers=self.headers,json={'text':'Привет!'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/history',headers=self.headers).json(),[])


if __name__ == '__main__':
    unittest.main()
