from pathlib import Path
import time
import unittest
import test_api
from test_core import RESULT
from app.storage import Repository


class BatchStatsTests(unittest.TestCase):
    setUp=test_api.ApiTests.setUp
    tearDown=test_api.ApiTests.tearDown

    def completed(self,labels,uid=None,filename='comments.csv'):
        repo=self.app.state.repository
        job=repo.create_job(uid or self.a['user']['id'],filename,len(labels))
        repo.complete_job(job,[f'Текст {i}' for i in range(len(labels))],
                          [dict(RESULT,label=label) for label in labels])
        return job

    def stats(self,jid,headers=None):
        return self.client.get(f'/api/batches/{jid}/stats',headers=headers or self.headers)

    def test_counts_and_shares_only_include_the_selected_file(self):
        repo=self.app.state.repository
        job=self.completed(['positive']*3+['neutral']*2+['negative'],filename='Мой файл.csv')
        self.completed(['negative']*4)
        repo.add_message(self.a['user']['id'],'Сообщение в чате',RESULT)
        response=self.stats(job['id']);self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['filename'],'Мой файл.csv')
        self.assertEqual(data['job_id'],job['id'])
        self.assertEqual(data['created_at'],job['created_at'])
        self.assertEqual(data['total'],6)
        self.assertEqual(data['counts'],{'positive':3,'neutral':2,'negative':1})
        self.assertEqual(data['shares'],{'positive':.5,'neutral':.3333,'negative':.1667})
        self.assertEqual(repo.get_chat_stats()['total'],1)
        self.assertEqual(repo.get_chat_stats()['batch_total'],10)

    def test_statistics_require_login_and_ownership(self):
        job=self.completed(['positive'])
        other={'Authorization':'Bearer '+self.b['token']}
        self.assertEqual(self.client.get(f"/api/batches/{job['id']}/stats").status_code,401)
        self.assertEqual(self.stats(job['id'],other).status_code,404)
        self.assertEqual(self.stats('missing').status_code,404)
        # административная роль не даёт доступа к чужим загрузкам
        member_job=self.completed(['negative'],uid=self.b['user']['id'])
        self.assertEqual(self.stats(member_job['id']).status_code,404)

    def test_statistics_are_unavailable_until_successful_completion(self):
        repo=self.app.state.repository
        job=repo.create_job(self.a['user']['id'],'a.txt',3)
        self.assertEqual(self.stats(job['id']).status_code,409)
        repo.update_job(job['id'],'running',1)
        self.assertEqual(self.stats(job['id']).status_code,409)
        repo.update_job(job['id'],'failed',error='Ошибка тестового задания')
        self.assertEqual(self.stats(job['id']).status_code,409)

    def test_uploaded_csv_is_counted_by_model_results_not_expected_labels(self):
        content='text;expected_label\na;negative\nb;negative\nc;neutral\n'
        response=self.client.post('/api/batches',headers=self.headers,files={'file':('check.csv',content)})
        self.assertEqual(response.status_code,202)
        jid=response.json()['id']
        for _ in range(100):
            status=self.client.get(f'/api/batches/{jid}',headers=self.headers).json()['status']
            if status=='done':break
            time.sleep(.01)
        self.assertEqual(status,'done')
        data=self.stats(jid).json()
        self.assertEqual(data['total'],3)
        self.assertEqual(data['counts'],{'positive':3,'neutral':0,'negative':0})
        self.assertEqual(data['shares']['positive'],1)

    def test_general_chat_clearing_does_not_change_file_statistics(self):
        job=self.completed(['positive','neutral','negative'])
        before=self.stats(job['id']).json()
        self.app.state.repository.add_message(self.a['user']['id'],'Общее сообщение',RESULT)
        self.assertEqual(self.client.post('/api/general/clear',headers=self.headers,
                                         json={'password':'password123','confirmed':True}).status_code,200)
        self.assertEqual(self.stats(job['id']).json(),before)

    def test_empty_aggregate_and_old_jobs_after_repository_restart(self):
        empty=self.completed([])
        data=self.stats(empty['id']).json()
        self.assertEqual(data['total'],0)
        self.assertEqual(sum(data['shares'].values()),0)
        job=self.completed(['positive','negative'])
        again=Repository(Path(self.tmp.name)/'chat.sqlite3')
        self.assertEqual(again.job_stats(job['id']),{'total':2,'counts':{'positive':1,'neutral':0,'negative':1},
                                                   'shares':{'positive':.5,'neutral':0,'negative':.5}})


if __name__=='__main__':unittest.main()
