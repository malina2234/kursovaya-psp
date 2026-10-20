from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.storage import Repository
from test_core import RESULT


class CalendarStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.repo=Repository(Path(self.tmp.name)/'chat.sqlite3')
        self.uid=self.repo.authenticate('Анна','password123',True)['user']['id']

    def tearDown(self):self.tmp.cleanup()

    def test_full_calendar_window_excludes_future_and_keeps_all_time_totals(self):
        current=datetime(2026,10,16,12,tzinfo=timezone.utc)
        for offset in range(31):
            for label in ('positive','negative','neutral'):
                item=self.repo.add_message(self.uid,'Текст',dict(RESULT,label=label))
                date=(current-timedelta(days=offset)).isoformat()
                with self.repo.connect() as db:db.execute('UPDATE messages SET created_at=? WHERE id=?',(date,item['id']))
        future=self.repo.add_message(self.uid,'Будущая дата',RESULT)
        with self.repo.connect() as db:db.execute('UPDATE messages SET created_at=? WHERE id=?',((current+timedelta(days=1)).isoformat(),future['id']))
        job=self.repo.create_job(self.uid,'a.txt',1);self.repo.complete_job(job,['Из файла'],[RESULT])
        with patch('app.storage.datetime') as clock:
            clock.now.return_value=current
            stats=self.repo.get_chat_stats()
        self.assertEqual(stats['total'],94)
        self.assertEqual(stats['batch_total'],1)
        self.assertEqual(stats['timeline_start'],'2026-09-17')
        self.assertEqual(stats['timeline_end'],'2026-10-16')
        self.assertEqual(len(stats['timeline']),90)
        self.assertEqual(sum(row['n'] for row in stats['timeline']),90)
        self.assertEqual({r['label'] for r in stats['timeline'] if r['day']=='2026-09-17'},{'positive','neutral','negative'})

    def test_empty_statistics_has_valid_calendar_and_zero_shares(self):
        stats=self.repo.get_chat_stats()
        self.assertEqual(stats['total'],0)
        self.assertEqual(stats['timeline'],[])
        self.assertEqual(sum(stats['shares'].values()),0)
        start=datetime.fromisoformat(stats['timeline_start'])
        end=datetime.fromisoformat(stats['timeline_end'])
        self.assertEqual((end-start).days,29)


if __name__=='__main__':unittest.main()
