import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
import runtime as r
import incremental_runtime as inc
from browser_transport import BrowserSession,TransportUnavailable,canonical_search_url

def row(native):
    return {'Stepstone_ID':native,'Titel_url':f'https://www.stepstone.de/stellenangebote--Test--{native}-inline.html','Job_Titel':'Rechtsanwalt','Name_des_Unternehmens':'Testkanzlei','Standort':'Berlin','Erscheinen':'heute','Home_möglich':''}

class IncrementalTests(unittest.TestCase):
    def harness(self,behavior):
        state={'queue':{},'jobs':{},'events':[],'summaries':[],'fetches':0}
        browser=SimpleNamespace(network_failures=0,restart_count=0,close=lambda:None)
        def new_browser(**kw):
            state['browser']=SimpleNamespace(network_failures=0,restart_count=0,close=lambda:None,next_search=lambda:state['events'].append('next_search'))
            return state['browser']
        conn=Mock();conn.execute.return_value.fetchone.return_value=(True,)
        class Store:
            def __init__(self,c):pass
            def history(self,*a):return []
            def cached(self):return {k:{'has_fulltext':True,'detail_quality':'fulltext','last_detail_check':r.now()}for k in state['jobs']}
            def checkpoint_page(self,rows,run_id,key,*a):
                state['queue'].setdefault(key,{})
                for x in rows:state['queue'][key][x['Stepstone_ID']]=x
                state['events'].append('checkpoint')
            def search_checkpoint(self,*a):pass
            def queued(self,key,run_id,resume=False):return list(state['queue'].get(key,{}).values())
            def save_job(self,x,d,run_id):
                state['jobs'][x['Stepstone_ID']]=d;state['events'].append('save')
            def touch(self,run_id,status,summary):state['summaries'].append((status,dict(summary)))
        class Client:
            def __init__(self,**kw):pass
            def fetch_search(self,spec,date,**kw):
                state['fetches']+=1
                return behavior(state,state['browser'],kw['on_page_rows'])
        details=SimpleNamespace(get=lambda *a:{'quality':'fulltext','text':'Original public job description'})
        stack=[patch.object(r.psycopg,'connect',return_value=conn),patch.object(r,'Store',Store),patch.object(r,'StepstoneClient',Client),patch.object(r,'DetailClient',return_value=details),patch.object(inc,'BrowserSession',side_effect=new_browser),patch.object(r.time,'sleep'),patch.object(r,'notify'),patch.dict(r.os.environ,{'STEPSTONE_DATABASE_URL':'test-only'}),patch('builtins.print')]
        return state,stack
    def run_with(self,stack,**args):
        from contextlib import ExitStack
        with ExitStack()as s:
            for p in stack:s.enter_context(p)
            return inc.run(r,**args)
    def test_first_search_text_is_committed_before_second_search_fails(self):
        def behavior(state,browser,emit):
            if state['fetches']==1:
                emit([row('100')]);return SimpleNamespace(rows=[row('100')],coverage_warnings=[],pages=1,inventory_complete=True,declared_total=1,offered_pages=1)
            self.assertIn('100',state['jobs'])
            browser.network_failures=2;raise r.requests.ReadTimeout('network failure')
        state,stack=self.harness(behavior)
        self.assertEqual(self.run_with(stack,limit=2),2)
        self.assertIn('100',state['jobs'])
        self.assertEqual(state['events'][:2],['checkpoint','save'])
        self.assertEqual(state['events'][2],'next_search')
    def test_resume_reads_durable_queue_without_new_listing_requests(self):
        def broken(state,browser,emit):
            emit([row('200')]);browser.network_failures=2;raise r.requests.ReadTimeout('network failure')
        state,stack=self.harness(broken)
        self.assertEqual(self.run_with(stack,limit=1),2)
        self.assertFalse(state['jobs']);self.assertTrue(state['queue'])
        before=state['fetches']
        # New runner/browser after interruption, same durable Store state.
        self.assertEqual(self.run_with(stack,limit=1,resume_only=True,max_details=1),0)
        self.assertEqual(state['fetches'],before)
        self.assertIn('200',state['jobs'])
    def test_database_failure_is_fatal_and_not_retried_as_a_network_problem(self):
        def broken(state,browser,emit):raise r.PersistenceError('checkpoint failed')
        state,stack=self.harness(broken)
        with self.assertRaisesRegex(RuntimeError,'PersistenceError'):self.run_with(stack,limit=1)
        self.assertEqual(state['fetches'],1)
        self.assertEqual(state['summaries'][-1][0],'failed')
    def test_resume_honors_total_detail_budget_instead_of_listing_sample_cap(self):
        def broken(state,browser,emit):
            emit([row(str(300+i))for i in range(7)])
            browser.network_failures=2;raise r.requests.ReadTimeout('network failure')
        state,stack=self.harness(broken)
        self.run_with(stack,limit=1)
        self.assertEqual(self.run_with(stack,repair=True,resume_only=True,max_details=5),0)
        self.assertEqual(len(state['jobs']),5)
        self.assertEqual(state['fetches'],1)
    def test_large_run_gate_prevents_connection_or_fetch(self):
        with patch.object(r,'POLICY',{**r.POLICY,'large_run_enabled':False}),patch.object(r,'notify'),patch.object(r.psycopg,'connect')as connect,patch('builtins.print'):
            self.assertEqual(inc.run(r),2);connect.assert_not_called()

class RecoveryTests(unittest.TestCase):
    def test_canonical_search_preserves_functional_filters_and_page(self):
        from urllib.parse import urlsplit,parse_qs
        url='https://www.stepstone.de/jobs/assistenz?action=facet_selected%3BherdwickDisciplines%3BLegal&hdi=Legal&searchOrigin=Resultlist_top-search&q=Legal+Assistenz&page=2&l=Berlin'
        q=parse_qs(urlsplit(canonical_search_url(url)).query)
        self.assertEqual(q,{'hdi':['Legal'],'q':['Legal Assistenz'],'page':['2'],'l':['Berlin']})
        self.assertIn('action=other',canonical_search_url('https://www.stepstone.de/jobs/legal?action=other'))
    def test_scope_boundary_releases_old_browser_but_retains_session(self):
        state={'cookies':[],'origins':[]};events=[]
        session=object.__new__(BrowserSession);session.network_failures=0;session.scope_restarts=0
        session.context=SimpleNamespace(storage_state=lambda:state,close=lambda:events.append('context_closed'))
        session.browser=SimpleNamespace(close=lambda:events.append('browser_closed'))
        session._open=lambda saved:events.append(('opened',saved))
        session.next_search()
        self.assertEqual(events,['context_closed','browser_closed',('opened',state)])
        self.assertEqual(session.scope_restarts,1)
    def test_failed_restart_opens_circuit_instead_of_repeating_requests(self):
        session=object.__new__(BrowserSession);session.manager=object();session.network_failures=0
        session.context=Mock();session.context.storage_state.side_effect=RuntimeError('closed driver')
        with self.assertRaises(TransportUnavailable):session._network_fault()
        self.assertEqual(session.network_failures,2)
    def test_reset_retains_session_state_and_second_fault_opens_circuit(self):
        storage={'cookies':[{'name':'public-preference','value':'de'}],'origins':[]}
        session=object.__new__(BrowserSession)
        session.manager=object();session.network_failures=0;session.restart_count=0
        session.context=Mock();session.context.storage_state.return_value=storage
        session.browser=Mock();session._open=Mock()
        with patch('browser_transport.time.sleep'):
            session._network_fault();session._network_fault()
        session._open.assert_called_once_with(storage)
        self.assertEqual(session.restart_count,1)
        with self.assertRaises(TransportUnavailable):session.get('https://www.stepstone.de/jobs/test')

if __name__=='__main__':unittest.main()
