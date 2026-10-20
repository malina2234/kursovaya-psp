import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from app.access import InternetAccess, instance_id, lan_addresses
import test_api
from fastapi.testclient import TestClient
from app.server import create_app


class AccessTests(unittest.TestCase):
    def test_extract_only_cloudflare_https_address(self):
        access=InternetAccess(8000)
        access._accept_line('https://malicious.example is not the address')
        self.assertIsNone(access.candidate)
        access._accept_line('Visit: https://fresh-chat-example.trycloudflare.com |')
        self.assertEqual(access.candidate,'https://fresh-chat-example.trycloudflare.com')
        access._accept_line('Registered tunnel connection connIndex=0')
        self.assertTrue(access.registered)

    def test_instance_is_data_directory_specific(self):
        self.assertEqual(instance_id('data'),instance_id(Path('data').resolve()))
        self.assertNotEqual(instance_id('data'),instance_id('demo_data'))

    def test_lan_is_disabled_for_loopback(self):
        self.assertEqual(lan_addresses(8000,'127.0.0.1'),[])

    def test_lan_addresses_exclude_vpn_and_loopback(self):
        addresses=[(socket.AF_INET,None,None,None,(a,0)) for a in ('192.168.0.102','127.0.0.1','100.77.1.1','169.254.1.2')]
        with patch('app.access.socket.getaddrinfo',return_value=addresses):
            self.assertEqual(lan_addresses(8000,'0.0.0.0'),['http://192.168.0.102:8000'])

    def test_missing_binary_does_not_stop_local_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            access=InternetAccess(8000,Path(tmp),True,Path(tmp)/'missing.exe')
            access._run()
            self.assertEqual(access.snapshot()['status'],'error')
            self.assertIn('cloudflared',access.snapshot()['error'])
            self.assertIsNone(access.snapshot()['url'])

    def test_reject_unverified_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'cloudflared.exe'
            binary.write_bytes(b'not the verified binary')
            access=InternetAccess(8000,Path(tmp),True,binary)
            access._run()
            self.assertEqual(access.snapshot()['status'],'error')
            self.assertIn('Контрольная сумма',access.snapshot()['error'])

    def test_close_invalidates_published_link_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            access=InternetAccess(8000,Path(tmp),True)
            access._set('online','https://test.trycloudflare.com')
            self.assertIn('https://test.',(Path(tmp)/'internet_link.txt').read_text(encoding='utf-8'))
            access.close()
            self.assertIsNone(access.snapshot()['url'])
            self.assertNotIn('https://test.',(Path(tmp)/'internet_link.txt').read_text(encoding='utf-8'))

    def test_access_status_requires_login(self):
        with tempfile.TemporaryDirectory() as tmp,TestClient(create_app(Path(tmp),test_api.StubAnalyzer())) as client:
            self.assertEqual(client.get('/api/access').status_code,401)
            user=client.post('/api/auth/register',json={'name':'Анна','password':'password123'}).json()
            response=client.get('/api/access',headers={'Authorization':'Bearer '+user['token']})
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json()['internet']['enabled'])
            self.assertFalse(response.json()['lan_enabled'])
            self.assertEqual(response.headers['cache-control'],'no-store, max-age=0')


if __name__=='__main__':unittest.main()
