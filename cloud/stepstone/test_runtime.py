import unittest,json
from runtime import extract_detail,anomalous,portal_url,sample_rows,ROOT,search_error_code
from kanzlei_discovery.stepstone import load_searches,search_fingerprint
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
