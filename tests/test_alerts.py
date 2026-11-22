import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import settings
from job_alerts.locations import location_matches,matching_locations
from job_alerts.matching import evaluate,required_years,merge_jobs,canonical_url,advanced_degree_required
from job_alerts.http import HttpClient,SourceError,retry_after
from job_alerts.sources import Collector
from job_alerts.storage import State,atomic_json
from job_alerts.delivery import build_email,configuration
from job_alerts.runner import deliver_profile,main
from job_alerts.semantic import parse_verdicts

NOW=1800000000


def job(title='Junior Software Engineer',desc='Bachelor degree in computer science. No experience required. Python and SQL.',loc='New York, NY',url='https://example.com/jobs/1',**extra):
    return {'company':'Example','title':title,'description':desc,'description_complete':True,'locations':[loc],'url':url,'posted':NOW-3600,'source':'fixture',**extra}


class MatchingTests(unittest.TestCase):
    def test_labeled_posting_regressions(self):
        fixtures=json.loads((Path(__file__).parent/'sample_jobs.json').read_text())
        for case in fixtures:
            for profile in ('tech','finance'):
                expected=case.get('expected_'+profile)
                if expected is not None:
                    with self.subTest(title=case['title'],profile=profile):
                        self.assertEqual(evaluate(case,profile,NOW) is not None,expected)

    def test_strict_default(self):
        self.assertIs(settings.NY_NJ_ONLY,True)
        for value in ('New York, NY','Jersey City, NJ','NYC','Remote — New Jersey','New York','NY / NJ'):
            with self.subTest(value=value):
                self.assertTrue(location_matches([value]))

    def test_strict_rejects_other_locations(self):
        for value in ('Boston, MA','San Francisco, CA (Hybrid)','Cambridge, United Kingdom','Tbilisi, Georgia','Remote (Switzerland)','US Remote','Worldwide','Remote','Newark, DE','Brooklyn, Australia'):
            with self.subTest(value=value):
                self.assertFalse(location_matches([value]))

    def test_broad_mode(self):
        for value in ('Boston, MA','Remote (United States)','Remote (Worldwide)'):
            self.assertTrue(location_matches([value],False))
        self.assertFalse(location_matches(['San Francisco, CA (Hybrid)'],False))
        self.assertFalse(location_matches(['Remote (Switzerland)'],False))

    def test_only_matched_locations_displayed(self):
        self.assertEqual(matching_locations(['New York, NY','Boston, MA','London, UK']),['New York, NY'])

    def test_new_grad_staff_title(self):
        self.assertIsNotNone(evaluate(job('Member of Technical Staff - New Grad'),'tech',NOW))

    def test_sql_server_title(self):
        self.assertIsNotNone(evaluate(job('SQL Server Developer I'),'tech',NOW))

    def test_senior_and_intern_rejected(self):
        for title in ('Staff Software Engineer','Senior Data Analyst','Software Engineer III','Software Engineer Intern'):
            self.assertIsNone(evaluate(job(title),'tech',NOW))

    def test_preferred_experience(self):
        desc='Bachelor degree in computer science. One year of experience preferred, not required.'
        self.assertIsNone(required_years(desc))
        self.assertIsNotNone(evaluate(job(desc=desc),'tech',NOW))

    def test_campus_cannot_override_experience(self):
        desc='Bachelor degree in computer science. Requires 5 years of software development experience. Our campus offers free parking.'
        self.assertEqual(required_years(desc),5)
        self.assertIsNone(evaluate(job('Software Engineer',desc),'tech',NOW))

    def test_entry_title_cannot_override_required_experience(self):
        self.assertIsNone(evaluate(job('Entry Level Software Engineer','Requires 3 years of professional experience.'),'tech',NOW))

    def test_required_experience_constraints(self):
        self.assertEqual(required_years('Requires 0-2 years of experience.'),0)
        self.assertEqual(required_years('Requires 5 years of experience. Requires 1 year of SQL experience.'),5)
        self.assertEqual(required_years('Requires five years of professional experience.'),5)
        self.assertIsNone(required_years('A 4 year degree is required. No experience required.'))

    def test_preferred_section(self):
        text='Required qualifications\nPython.\nPreferred qualifications\nThree years of experience.\nMinimum qualifications\nOne year of SQL experience.'
        self.assertEqual(required_years(text),1)

    def test_advanced_degree(self):
        self.assertFalse(advanced_degree_required('Master degree preferred.'))
        self.assertTrue(advanced_degree_required('PhD required.'))
        self.assertFalse(advanced_degree_required('Bachelor or master degree required.'))

    def test_more_role_families(self):
        for title in ('Implementation Consultant','Associate Product Manager','Business Systems Analyst','MLOps Engineer','Technology Risk Analyst','Technical Account Manager'):
            with self.subTest(title=title):
                self.assertIsNotNone(evaluate(job(title),'tech',NOW))

    def test_finance_roles(self):
        for title in ('Junior Credit Analyst','Graduate Operations Analyst','Product Analyst','Project Coordinator'):
            self.assertIsNotNone(evaluate(job(title,'Entry-level role. No experience required. Excel, SQL and financial analysis.'),'finance',NOW))

    def test_unknown_experience_labeled(self):
        desc='Build and maintain Python data pipelines and SQL reports for internal users. Collaborate with business teams to document requirements. Bachelor degree in computer science.'
        result=evaluate(job('Data Analyst',desc),'tech',NOW)
        self.assertEqual(result['confidence'],'review')
        self.assertTrue(any('not specified' in x for x in result['reasons']))

    def test_incomplete_unknown_rejected(self):
        self.assertIsNone(evaluate(job('Data Analyst','Python and SQL',description_complete=False),'tech',NOW))

    def test_requisitions_are_distinct(self):
        self.assertEqual(len(merge_jobs([job(),job(url='https://example.com/jobs/2',loc='Jersey City, NJ')])),2)

    def test_merge_richer_copy_before_filtering(self):
        weak=job('Software Engineer','',description_complete=False)
        strong=job('Software Engineer')
        merged=merge_jobs([weak,strong])
        self.assertEqual(len(merged),1)
        self.assertIsNotNone(evaluate(merged[0],'tech',NOW))

    def test_tracking_query_not_job_id_removed(self):
        self.assertEqual(canonical_url('https://example.com/jobs?id=42&utm_source=x'),'https://example.com/jobs?id=42')
        self.assertEqual(canonical_url('javascript:alert(1)'),'')

    def test_html_is_escaped(self):
        result=evaluate(job(),'tech',NOW)
        result['company']='<img src=x onerror=alert(1)>'
        body=build_email('tech',[result])[1]
        self.assertNotIn('<img',body)
        self.assertIn('&lt;img',body)

    def test_ai_boolean_validation(self):
        with self.assertRaises(ValueError):
            parse_verdicts('[{"n":0,"qualified":"false"}]',1)
        self.assertEqual(parse_verdicts('[{"n":0,"qualified":false}]',1),{0:False})


class StateTests(unittest.TestCase):
    def test_email_requires_private_configuration(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):
                configuration('tech')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_migration(self):
        atomic_json(self.home/'seen_jobs.json',['example|junior software engineer','https://example.com/jobs/1'])
        state=State(self.home)
        state.enqueue('tech',[evaluate(job(),'tech',NOW)],NOW)
        self.assertEqual(state.profile('tech')['pending'],{})
        state.enqueue('tech',[evaluate(job(url='https://example.com/jobs/2'),'tech',NOW)],NOW)
        self.assertEqual(len(state.profile('tech')['pending']),1)

    def test_corrupt_state_fails_loudly(self):
        (self.home/'alerts_v2.json').write_text('{broken')
        with self.assertRaises(RuntimeError):
            State(self.home)

    def test_deliveries_per_recipient(self):
        state=State(self.home)
        j=evaluate(job(),'tech',NOW)
        state.enqueue('tech',[j],NOW)
        state.delivered('tech','a@example.com',[j],NOW)
        self.assertEqual(state.pending('tech','a@example.com'),[])
        self.assertEqual(len(state.pending('tech','b@example.com')),1)
        self.assertNotIn('a@example.com',state.path.read_text())

    def test_application_status_suppresses(self):
        state=State(self.home)
        j=evaluate(job(),'tech',NOW)
        state.enqueue('tech',[j],NOW)
        state.track('tech',j['url'],'applied')
        self.assertEqual(state.pending('tech','a@example.com'),[])
        state.track('tech',j['url'],'reset')
        self.assertEqual(len(state.pending('tech','a@example.com')),1)

    def test_overflow_remains_queued(self):
        state=State(self.home)
        jobs=[evaluate(job(url='https://example.com/jobs/'+str(i)),'tech',NOW) for i in range(3)]
        state.enqueue('tech',jobs,NOW)
        with patch('job_alerts.runner.configuration',return_value=('sender@example.com',['a@example.com'],'fake')),patch.object(settings,'MAX_EMAIL_JOBS',1):
            calls=[]
            deliver_profile(state,'tech',NOW,send=lambda *a:calls.append(a))
        self.assertEqual(len(calls),1)
        self.assertEqual(len(state.pending('tech','a@example.com')),2)

    def test_partial_send_failure_keeps_unsent(self):
        state=State(self.home)
        state.enqueue('tech',[evaluate(job(),'tech',NOW)],NOW)
        def send(sender,recipient,*args):
            if recipient=='b@example.com':
                raise RuntimeError('test failure')
        with patch('job_alerts.runner.configuration',return_value=('sender@example.com',['a@example.com','b@example.com'],'fake')):
            with self.assertRaises(RuntimeError):
                deliver_profile(state,'tech',NOW,send=send)
        restored=State(self.home)
        self.assertEqual(restored.pending('tech','a@example.com'),[])
        self.assertEqual(len(restored.pending('tech','b@example.com')),1)

    def test_switch_reapplied_to_pending(self):
        state=State(self.home)
        with patch.object(settings,'NY_NJ_ONLY',False):
            j=evaluate(job(loc='Boston, MA'),'tech',NOW)
        state.enqueue('tech',[j],NOW)
        with patch('job_alerts.runner.configuration',return_value=('sender@example.com',['a@example.com'],'fake')):
            calls=[]
            deliver_profile(state,'tech',NOW,send=lambda *args:calls.append(args))
        self.assertEqual(calls,[])

    def test_preview_no_delivery_or_state_changes(self):
        fixture=self.home/'input.json'
        fixture.write_text(json.dumps([job(posted=None)]))
        with patch.dict(os.environ,{'JOB_ALERT_HOME':str(self.home/'state')}),patch('sys.argv',['runner','--input',str(fixture),'--output',str(self.home/'output'),'--dry-run']),patch('job_alerts.runner.send_email') as send,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(),0)
        self.assertFalse(send.called)
        self.assertFalse((self.home/'state'/'alerts_v2.json').exists())


class HttpTests(unittest.TestCase):
    def test_cache_reuses_request(self):
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def open(req,timeout):
                calls.append(req)
                return contextlib.closing(io.BytesIO(b'{"jobs":[]}'))
            client=HttpClient(temp,{},opener=open)
            self.assertEqual(client.get('https://example.com/api'),{'jobs':[]})
            client.get('https://example.com/api')
            self.assertEqual(len(calls),1)
            self.assertEqual(client.hits,1)

    def test_retry_after_deferred_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def open(req,timeout):
                calls.append(req)
                raise urllib.error.HTTPError(req.full_url,429,'limited',{'Retry-After':'120'},None)
            cooldown={}
            client=HttpClient(temp,cooldown,opener=open,clock=lambda:NOW,sleeper=lambda s:None)
            with self.assertRaises(SourceError):
                client.get('https://example.com/api')
            self.assertEqual(cooldown['example.com'],NOW+900)
            with self.assertRaises(SourceError):
                client.get('https://example.com/second')
            self.assertEqual(len(calls),1)

    def test_retry_after_date(self):
        self.assertEqual(retry_after('120',NOW),120)
        self.assertEqual(retry_after('invalid',NOW),0)


class SourceTests(unittest.TestCase):
    def test_workday_pagination_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            state=State(temp)
            class Client:
                def __init__(self): self.offsets=[]
                def get(self,url,payload=None,**kwargs):
                    self.offsets.append(payload['offset'])
                    return {'total':100,'jobPostings':[{'title':'Senior Engineer','externalPath':'/job/'+str(i),'locationsText':'NY'} for i in range(20)]}
            client=Client()
            collector=Collector(client,state,NOW)
            with patch.object(settings,'MAX_PAGES_PER_SOURCE',2):
                list(collector.workday(('example','wd1','Careers')))
                list(collector.workday(('example','wd1','Careers')))
            self.assertEqual(client.offsets,[0,20,40,60])

    def test_partial_results_survive_later_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            collector=Collector(None,State(temp),NOW)
            def source():
                yield job()
                raise SourceError('HTTP 503')
            result=collector.run('example',source)
            self.assertEqual(len(result),1)
            self.assertEqual(collector.reports[0]['status'],'failed')

    def test_greenhouse_full_content_and_honest_date(self):
        class Client:
            def get(self,url,**kwargs):
                self.url=url
                return {'jobs':[{'id':1,'title':'Software Engineer I','location':{'name':'New York, NY'},'absolute_url':'https://example.com/1','updated_at':'2026-11-22T00:00:00Z','content':'No experience required. Python.'}]}
        with tempfile.TemporaryDirectory() as temp:
            client=Client()
            result=list(Collector(client,State(temp),NOW).greenhouse('example'))
            self.assertIn('content=true',client.url)
            self.assertIsNone(result[0]['posted'])
            self.assertTrue(result[0]['description_complete'])

    def test_remoteok_foreign_location(self):
        class Client:
            def get(self,*args,**kwargs):
                return [{'position':'Junior Software Engineer','company':'Example','location':'Switzerland','url':'https://example.com/1','description':'No experience required. Python.'}]
        with tempfile.TemporaryDirectory() as temp:
            jobs=list(Collector(Client(),State(temp),NOW).remoteok())
            self.assertIsNone(evaluate(jobs[0],'tech',NOW))


if __name__=='__main__':
    unittest.main()
