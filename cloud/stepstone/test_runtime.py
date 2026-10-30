import unittest,json
from runtime import extract_detail,anomalous,portal_url,sample_rows,ROOT,search_error_code,detail_priority,now,capture_page,TimeBudgetReached
from kanzlei_discovery.stepstone import load_searches,search_fingerprint
from kanzlei_discovery.stepstone import parse_result_state
from browser_transport import timeout_milliseconds,BrowserSession
URL='https://www.stepstone.de/stellenangebote--Test--12345678-inline.html'
TEXT='Ihre Aufgaben Sie beraten in Rechtsfragen. Ihr Profil Sie sind Volljurist. Wir bieten gute Bedingungen. '*10
class DetailTests(unittest.TestCase):
    def schema(self,**overrides):
        j={'@type':'JobPosting','url':URL,'title':'Volljurist','description':TEXT,**overrides}
        return '<script type="application/ld+json">'+json.dumps(j)+'</script>'
    def test_fulltext(self):self.assertEqual(extract_detail(self.schema(),URL,'12345678')['quality'],'fulltext')
    def test_other_job(self):self.assertIsNone(extract_detail(self.schema(url=URL.replace('12345678','99999999')),URL,'12345678')['text'])
    def test_snippet_not_fulltext(self):self.assertIsNone(extract_detail(self.schema(description='Ein kurzer Suchtreffer'),URL,'12345678')['text'])
    def test_wrong_native_id(self):self.assertIsNone(extract_detail(self.schema(identifier={'value':'99999999'}),URL,'12345678')['text'])
    def test_employer_url_not_visited(self):self.assertFalse(portal_url('https://example.com/job/12345678'))
    def test_article_not_job(self):self.assertIsNone(extract_detail('<article>'+TEXT+'</article>',URL,'12345678')['text'])
    def test_legacy_section(self):self.assertEqual(extract_detail('<div data-at="job-ad-content">'+TEXT+'</div>',URL,'12345678')['quality'],'fulltext')
    def test_graph(self):
        j={'@graph':[{'@type':'Organization'},{'@type':'JobPosting','url':URL,'description':TEXT}]}
        self.assertEqual(extract_detail('<script type="application/ld+json">'+json.dumps(j)+'</script>',URL,'12345678')['quality'],'fulltext')
    def test_drop(self):self.assertTrue(anomalous(50,[100,110,95,100]))
    def test_spike(self):self.assertTrue(anomalous(160,[100,100]))
    def test_normal_noise(self):self.assertFalse(anomalous(105,[100,110]))
    def test_small_counts(self):self.assertFalse(anomalous(5,[10]))
    def test_first_run_no_fake_baseline(self):self.assertFalse(anomalous(50,[]))
    def test_zero_always_alerts(self):self.assertTrue(anomalous(0,[4,5,4]))
    def test_browser_read_timeout_tuple(self):self.assertEqual(timeout_milliseconds((10,30)),30000)
    def test_browser_search_timeout(self):self.assertEqual(timeout_milliseconds(35),35000)
    def test_network_diagnostic_does_not_leak_arbitrary_messages(self):
        e=RuntimeError('secret');e.__cause__=ConnectionError('net::ERR_HTTP2_PROTOCOL_ERROR password=secret')
        code=search_error_code(e)
        self.assertIn('ERR_HTTP2_PROTOCOL_ERROR',code)
        self.assertNotIn('secret',code)
    def test_browser_preserves_http2_error_and_closes_failed_page(self):
        class Error(Exception):pass
        class Page:
            closed=False
            def goto(self,*a,**kw):raise Error('net::ERR_HTTP2_PROTOCOL_ERROR at secret-url')
            def close(self):self.closed=True
        page=Page()
        class Context:
            def new_page(self):return page
        session=object.__new__(BrowserSession);session.context=Context()
        try:session.get('https://www.stepstone.de/jobs/test')
        except Exception as exc:
            self.assertIn('ERR_HTTP2_PROTOCOL_ERROR',search_error_code(exc))
            self.assertNotIn('secret-url',str(exc))
        else:self.fail('network failure swallowed')
        self.assertTrue(page.closed)
    def test_new_and_failed_descriptions_are_not_starved_by_old_cached_jobs(self):
        rows=[{'Stepstone_ID':str(i),'Titel_url':URL}for i in range(1,5)]
        cache={'1':{'has_fulltext':True,'detail_quality':'fulltext','last_detail_check':now()},'2':{'has_fulltext':True,'detail_quality':'network_error','last_detail_check':now()},'3':{'has_fulltext':False,'detail_quality':'missing_fulltext'}}
        order=[r['Stepstone_ID']for r in sorted(rows,key=lambda r:detail_priority(r,cache))]
        self.assertEqual(order[-2:],['2','1'])
        self.assertEqual(set(order[:2]),{'3','4'})
    def test_deadline_preserves_just_received_page(self):
        captured={};row={'Stepstone_ID':'12345678','Titel_url':URL}
        with self.assertRaises(TimeBudgetReached):capture_page(captured,[row],0)
        self.assertEqual(list(captured),['12345678'])
    def test_targeted_readiness_accepts_payload_without_full_dom_event(self):
        from types import SimpleNamespace
        state={'searchResults':{'items':[{'id':1}],'pagination':{'page':1,'pageCount':2}}}
        class Page:
            url='https://www.stepstone.de/jobs/test'
            closed=False
            def goto(self,url,**kw):
                if kw['wait_until']!='commit':raise TimeoutError('unrelated resource stalled')
                return SimpleNamespace(status=200,headers={'content-type':'text/html'})
            def wait_for_function(self,*a,**kw):pass
            def content(self):return '<html>still parsing other scripts</html>'
            def evaluate(self,*a):return state
            def close(self):self.closed=True
        page=Page();session=object.__new__(BrowserSession)
        session.context=SimpleNamespace(new_page=lambda:page)
        response=session.get(page.url)
        self.assertEqual(parse_result_state(response.text),state)
        self.assertTrue(page.closed)
    def test_missing_ready_payload_is_not_a_zero_inventory(self):
        from types import SimpleNamespace
        class Page:
            url='https://www.stepstone.de/jobs/test'
            def goto(self,*a,**kw):return SimpleNamespace(status=200,headers={'content-type':'text/html'})
            def wait_for_function(self,*a,**kw):raise TimeoutError('not ready')
            def content(self):return '<html>loading</html>'
            def evaluate(self,*a):return None
            def close(self):pass
        page=Page();session=object.__new__(BrowserSession)
        session.context=SimpleNamespace(new_page=lambda:page)
        response=session.get(page.url)
        with self.assertRaises(RuntimeError):parse_result_state(response.text)
    def test_survey_limits_each_search_before_global_dedupe(self):
        a=[{'id':i}for i in range(25)]
        self.assertEqual(len(sample_rows(a,2)),2)
        self.assertIs(sample_rows(a),a)
    def test_candidate_scope_keeps_existing_filter_and_changes_baseline(self):
        old=load_searches(ROOT/'config/stepstone_searches.json')[:7]
        new=load_searches(ROOT/'config/stepstone_searches_candidate.json')
        self.assertEqual(new[:len(old)],old)
        self.assertGreater(len(new),len(old))
        self.assertEqual(len({s.name for s in new}),len(new))
        self.assertNotEqual(search_fingerprint(old),search_fingerprint(new))
if __name__=='__main__':unittest.main()
