"""Regression cases for shared matching and the reported Adzuna alert issues."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings
from job_alerts.delivery import build_email, salary_label
from job_alerts.matching import evaluate, job_key, match_profiles, merge_jobs
from job_alerts.runner import main
from job_alerts.sources import Collector
from job_alerts.storage import State, recipient_key

NOW=1800000000
EY_TITLE='Government and Infrastructure - Assurance - Analyst 1'
EY_URL='https://www.adzuna.com/land/ad/5974121236?se=search-a&v=redirect-a'


def job(title='Junior Financial Analyst',description=None,**extra):
    if description is None:
        description='No experience required. Bachelor degree in any discipline. Analyze budgets and build Excel reports.'
    return {'company':'Example','title':title,'description':description,
            'description_complete':True,'locations':['New York, NY'],
            'url':'https://example.com/jobs/1','posted':None,'source':'fixture',**extra}


class CrossoverTests(unittest.TestCase):
    def test_source_does_not_limit_destination_profile(self):
        rows=[job(source='Finance discovery'),
              job('Junior Business Analyst',url='https://example.com/jobs/2',source='Tech discovery')]
        matches=match_profiles(rows,NOW)
        for profile in ('tech','finance'):
            self.assertEqual(len(matches[profile]),2)
            for result in matches[profile]:
                self.assertEqual(result['matching_profiles'],['tech','finance'])
                self.assertTrue(any('Also matches' in r for r in result['reasons']))

    def test_analyst_with_open_degree_reaches_both_profiles(self):
        for title in ('Junior Financial Analyst','Consulting Analyst I','Junior Strategy Analyst','Assurance Analyst I'):
            with self.subTest(title=title):
                self.assertIsNotNone(evaluate(job(title),'tech',NOW))
                self.assertIsNotNone(evaluate(job(title),'finance',NOW))

    def test_explicit_finance_only_degree_does_not_become_cs_match(self):
        j=job(description='No experience required. A degree in finance or accounting is required. Excel and financial analysis.')
        self.assertIsNone(evaluate(j,'tech',NOW))
        self.assertIsNotNone(evaluate(j,'finance',NOW))

    def test_shared_titles_still_respect_profile_degree_requirements(self):
        j=job('Junior Risk Analyst','No experience required. A degree in accounting or finance is required. Excel and SQL.')
        self.assertIsNone(evaluate(j,'tech',NOW))
        self.assertIsNotNone(evaluate(j,'finance',NOW))
        j=job('Junior Data Analyst','No experience required. A degree in computer science is required. Python and SQL.')
        self.assertIsNotNone(evaluate(j,'tech',NOW))
        self.assertIsNone(evaluate(j,'finance',NOW))

    def test_unspecified_degree_is_review_not_confirmed_eligibility(self):
        j=job(description='No experience required. Analyze corporate budgets, prepare financial reports, review forecasts and work with clients to improve business processes.')
        result=evaluate(j,'tech',NOW)
        self.assertEqual(result['confidence'],'review')
        self.assertLessEqual(result['score'],70)

    def test_generic_commercial_store_role_not_added_to_tech(self):
        j=job('Commercial Specialist','Maintain store vehicle fleet and deliver automotive parts to customers. Operate POS systems, stock shelves and help walk-in customers. High school diploma required. Excel required.')
        self.assertIsNone(evaluate(j,'tech',NOW))

    def test_technical_analytics_role_can_reach_finance_with_evidence(self):
        j=job('Junior Data Scientist - Credit Risk','No experience required. Bachelor degree in computer science, finance, economics or statistics. Python, SQL, financial analysis and risk management.')
        matches=match_profiles([j],NOW)
        self.assertEqual(len(matches['tech']),1)
        self.assertEqual(len(matches['finance']),1)

    def test_software_role_at_bank_not_automatically_finance(self):
        j=job('Junior Software Engineer','No experience required. Computer science degree. Build APIs with Python and SQL for financial analysis.',company='Example Bank')
        self.assertIsNotNone(evaluate(j,'tech',NOW))
        self.assertIsNone(evaluate(j,'finance',NOW))

    def test_crossover_can_be_disabled(self):
        with patch.object(settings,'INCLUDE_CROSSOVER_ROLES',False):
            self.assertIsNone(evaluate(job(),'tech',NOW))
            self.assertIsNotNone(evaluate(job(),'finance',NOW))

    def test_ey_snippet_is_review_in_both_profiles(self):
        j=job(EY_TITLE,'Our government assurance team works with clients.',company='EY',url=EY_URL,source='Adzuna',description_complete=False)
        for profile in ('tech','finance'):
            result=evaluate(j,profile,NOW)
            self.assertIsNotNone(result)
            self.assertEqual(result['confidence'],'review')
            self.assertLessEqual(result['score'],60)
            self.assertIn('Full requirements unavailable — verify before applying',result['reasons'])

    def test_ey_full_requirements_override_analyst_one_title(self):
        desc='To qualify for the role you must have\nA degree in Accounting, Finance, or related field.\nApproximately 1 year of related work experience.\nSufficient course work to achieve CPA certification upon passing the examination.'
        j=job(EY_TITLE,desc)
        self.assertIsNone(evaluate(j,'tech',NOW))
        self.assertIsNone(evaluate(j,'finance',NOW))

    def test_required_cpa_coursework_blocks_tech_crossover(self):
        desc='No experience required. Bachelor degree in any discipline. Sufficient course work to achieve CPA certification upon passing the examination.'
        self.assertIsNone(evaluate(job(EY_TITLE,desc),'tech',NOW))

    def test_optional_cpa_does_not_block_crossover(self):
        desc='No experience required. Bachelor degree in any discipline.\nPreferred qualifications\nCPA eligibility.'
        self.assertIsNotNone(evaluate(job('Assurance Analyst I',desc),'tech',NOW))

    def test_location_and_experience_limits_still_apply_to_both(self):
        for j in (job(locations=['Boston, MA']),job(description='Bachelor degree in any discipline. Requires 3 years of experience.')):
            self.assertEqual(match_profiles([j],NOW),{'tech':[],'finance':[]})

    def test_runner_reports_shared_matches_without_sending(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            fixture=root/'jobs.json'
            fixture.write_text(json.dumps([job()]),encoding='utf-8')
            with patch.dict(os.environ,{'JOB_ALERT_HOME':str(root/'state')}),patch('sys.argv',['runner','--input',str(fixture),'--output',str(root/'out'),'--dry-run']),patch('job_alerts.runner.send_email') as send,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
            summary=json.loads((root/'out'/'health.json').read_text())['summary']
            self.assertEqual(summary['shared_matches'],1)
            self.assertEqual(summary['profiles']['tech']['matches'],1)
            self.assertEqual(summary['profiles']['finance']['matches'],1)
            self.assertFalse(send.called)
            self.assertFalse((root/'state'/'alerts_v2.json').exists())


class AdzunaTests(unittest.TestCase):
    def test_predicted_salary_flags_normalized(self):
        for flag,expected in [(1,True),('1',True),(0,False),('0',False),(None,None)]:
            with self.subTest(flag=flag),tempfile.TemporaryDirectory() as temp:
                class Client:
                    def get(self,*args,**kwargs):
                        return {'count':1,'results':[{'id':'5974121236','title':EY_TITLE,'company':{'display_name':'EY'},'location':{'display_name':'Hoboken, NJ'},'redirect_url':EY_URL,'description':'Assurance team.','salary_min':156358.62,'salary_max':156358.62,'salary_is_predicted':flag}]}
                with patch.dict(os.environ,{'ADZUNA_APP_ID':'fixture','ADZUNA_APP_KEY':'fixture'}):
                    row=list(Collector(Client(),State(temp),NOW).adzuna())[0]
                self.assertIs(row['salary']['is_estimate'],expected)
                self.assertFalse(row['description_complete'])
                self.assertEqual(row['job_id'],'5974121236')
                if expected is not False:
                    self.assertNotIn('156',salary_label(row))
                    self.assertIn('not confirmed',salary_label(row))
                else:
                    self.assertEqual(salary_label(row),'USD 156,359 / year')

    def test_old_adzuna_salary_without_provenance_hidden(self):
        j=job(source='Adzuna',salary={'min':207181.03,'max':207181.03,'currency':'USD','period':'year'})
        self.assertEqual(salary_label(j),'Salary not confirmed by employer')

    def test_confirmed_salary_readable(self):
        j=job(salary={'min':77200,'max':115500,'currency':'USD','period':'year'})
        self.assertEqual(salary_label(j),'USD 77,200–115,500 / year')

    def test_estimated_pay_cannot_filter_out_role(self):
        with patch.object(settings,'MIN_ANNUAL_SALARY',{'tech':100000}):
            j=job(source='Adzuna',salary={'min':70000,'max':80000,'currency':'USD','period':'year','is_estimate':True})
            self.assertIsNotNone(evaluate(j,'tech',NOW))
            j['salary']['is_estimate']=False
            self.assertIsNone(evaluate(j,'tech',NOW))

    def test_adzuna_tracking_variants_merge_keep_working_link(self):
        first=job(url=EY_URL,source='Adzuna')
        second=job(url=EY_URL.replace('search-a','search-b'),source='Adzuna')
        merged=merge_jobs([first,second])
        self.assertEqual(len(merged),1)
        self.assertEqual(job_key(first),job_key(second))
        self.assertIn('v=redirect-a',merged[0]['url'])
        self.assertNotEqual(job_key(first),job_key(job(url=EY_URL.replace('5974121236','5974083722'))))

    def test_complete_requirements_preferred_to_longer_snippet(self):
        snippet=job(description='Our dynamic team works with clients. '*30,description_complete=False)
        full=job(description='A finance degree and 4 years of experience are required.')
        merged=merge_jobs([snippet,full])
        self.assertEqual(merged[0]['description'],full['description'])
        self.assertIsNone(evaluate(merged[0],'finance',NOW))

    def test_existing_sent_history_and_tracking_migrate(self):
        with tempfile.TemporaryDirectory() as temp:
            state=State(temp)
            rid=recipient_key('reader@example.com')
            p=state.profile('tech')
            p['sent'][EY_URL]={rid:NOW}
            p['pending'][EY_URL]=evaluate(job(url=EY_URL),'tech',NOW)
            p['applications'][EY_URL]={'status':'applied','updated_at':NOW}
            p['legacy_urls']=[EY_URL]
            state.save()
            restored=State(temp)
            self.assertEqual(restored.profile('tech')['sent'][job_key(job(url=EY_URL))][rid],NOW)
            self.assertEqual(restored.pending('tech','reader@example.com'),[])
            alternate=EY_URL.replace('search-a','search-b')
            restored.track('tech',alternate,'reset')
            self.assertEqual(restored.pending('tech','reader@example.com'),[])
            self.assertEqual(len(restored.pending('tech','another@example.com')),1)
            restored.enqueue('tech',[evaluate(job(url=alternate),'tech',NOW)],NOW)
            self.assertEqual(len(restored.profile('tech')['pending']),1)

    def test_related_location_listings_grouped_without_lost_links(self):
        rows=[job(EY_TITLE,company='EY',url=EY_URL,locations=['Hoboken, NJ'],source='Adzuna',description_complete=False),
              job(EY_TITLE,company='EY',url=EY_URL.replace('5974121236','5974083722'),locations=['Grand Central, Manhattan'],source='Adzuna',description_complete=False)]
        matched=[evaluate(j,'finance',NOW) for j in merge_jobs(rows)]
        self.assertEqual(len(matched),2)
        _,body,text=build_email('finance',matched)
        self.assertEqual(body.count(EY_TITLE),1)
        self.assertEqual(text.count(EY_TITLE),1)
        self.assertIn('may be separate openings',body)
        for token in ('5974121236','5974083722','Hoboken','Grand Central'):
            self.assertIn(token,body)
            self.assertIn(token,text)


if __name__=='__main__':
    unittest.main()
