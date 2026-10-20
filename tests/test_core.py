from pathlib import Path
import tempfile
import unittest
from app.batch import parse_file
from app.sentiment import validate_text
from app.storage import Repository, classify_counts

RESULT = {'label':'positive', 'confidence':.9, 'scores':{'positive':.9,'negative':.1,'neutral':.2}, 'truncated':False}


class TextTests(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(validate_text('  Привет!  '), 'Привет!')

    def test_invalid_text(self):
        for value in ('  ', None, 3, 'x'*2001):
            with self.subTest(value=str(value)[:10]), self.assertRaises(ValueError):
                validate_text(value)

    def test_txt_bom_and_empty_lines(self):
        self.assertEqual(parse_file('a.txt', '\ufeffПривет\r\n\r\nПока'.encode()), ['Привет','Пока'])

    def test_csv_quoted_semicolon(self):
        self.assertEqual(parse_file('a.csv', 'text;note\n"Привет; мир";1'.encode()), ['Привет; мир'])

    def test_json_formats(self):
        self.assertEqual(parse_file('a.json', '["один",{"text":"два"}]'.encode()), ['один','два'])

    def test_invalid_files(self):
        cases = [('a.txt', b''), ('a.exe', b'abc'), ('a.txt', b'\xff'), ('a.csv', b'other\nhello'),
                 ('a.json', b'{}'), ('a.json', b'['), ('a.json', b'[null]'), ('a.txt', b'x'*1048577),
                 ('a.txt', ('x\n'*1001).encode()), ('a.json', b'[" "]')]
        for filename, content in cases:
            with self.subTest(file=filename, content=content[:30]), self.assertRaises(ValueError):
                parse_file(filename, content)


class ClassificationTests(unittest.TestCase):
    def test_insufficient(self):
        self.assertEqual(classify_counts({'positive':4,'negative':0,'neutral':0})['category'], 'insufficient')

    def test_threshold(self):
        self.assertEqual(classify_counts({'positive':3,'negative':2,'neutral':0})['category'], 'positive')
        self.assertEqual(classify_counts({'positive':2,'negative':3,'neutral':0})['category'], 'negative')

    def test_mixed(self):
        self.assertEqual(classify_counts({'positive':2,'negative':2,'neutral':1})['category'], 'mixed')

    def test_threshold_uses_exact_ratio(self):
        self.assertEqual(classify_counts({'positive':59999,'negative':40001,'neutral':0})['category'], 'mixed')

    def test_zero_division(self):
        self.assertEqual(classify_counts({'positive':0,'negative':0,'neutral':0})['shares']['positive'], 0)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'chat.db'
        self.repo = Repository(self.path)
        self.auth = self.repo.authenticate('Анна', 'password123', True)
        self.uid = self.auth['user']['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_auth_and_password(self):
        self.assertEqual(self.repo.user_by_token(self.auth['token'])['id'], self.uid)
        with self.assertRaises(ValueError):
            self.repo.authenticate('Анна', 'incorrect')
        self.assertEqual(self.repo.authenticate('АННА', 'password123')['user']['id'], self.uid)
        with self.assertRaises(ValueError):
            self.repo.authenticate('анна', 'password123', True)

    def test_revoke(self):
        self.repo.revoke(self.auth['token'])
        self.assertIsNone(self.repo.user_by_token(self.auth['token']))

    def test_persistence_and_stats(self):
        self.repo.add_message(self.uid, '<script>hello</script>', RESULT)
        again = Repository(self.path)
        self.assertEqual(again.history()[0]['text'], '<script>hello</script>')
        self.assertEqual(again.get_chat_stats()['counts']['positive'], 1)

    def test_batch_is_separate_from_chat(self):
        job = self.repo.create_job(self.uid, 'a.txt', 5)
        self.repo.complete_job(job, ['a']*5, [RESULT]*5)
        self.assertEqual(self.repo.get_chat_stats()['total'], 0)
        self.assertEqual(self.repo.classify_user(self.uid)['category'], 'positive')
        self.assertEqual(len(self.repo.job_results(job['id'])), 5)

    def test_interrupted_job_recovery(self):
        job = self.repo.create_job(self.uid, 'a.txt', 3)
        self.repo.update_job(job['id'], 'running', 2)
        again = Repository(self.path)
        self.assertEqual(again.job(job['id'], self.uid)['status'], 'failed')
        self.assertEqual(again.classify_user(self.uid)['total'], 0)

    def test_history_pagination(self):
        ids = [self.repo.add_message(self.uid, str(i), RESULT)['id'] for i in range(6)]
        self.assertEqual([r['id'] for r in self.repo.history(before=ids[3],limit=2)], ids[1:3])


if __name__ == '__main__':
    unittest.main()
