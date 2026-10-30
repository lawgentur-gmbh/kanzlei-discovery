"""Normal Chromium page navigation for timeout/JS-layout recovery. No stealth.

No proxies, user-agent rotation, CAPTCHA solving or access-control bypass.
Only public StepStone HTTPS pages, images/fonts/media not downloaded.
"""
import json,re,time
from dataclasses import dataclass
from kanzlei_discovery.stepstone import STATE_MARKER
from urllib.parse import urlsplit,parse_qs
import requests

class TransportUnavailable(requests.ConnectionError):pass

@dataclass
class PageResponse:
    url:str
    text:str
    status_code:int
    headers:dict
    def raise_for_status(self):
        if self.status_code>=400:raise requests.HTTPError(f'HTTP {self.status_code}')

class BrowserSession:
    def __init__(self,http2=True,engine='chromium'):
        from playwright.sync_api import sync_playwright
        self.manager=sync_playwright().start();self.http2=http2;self.engine=engine;self.network_failures=0;self.restart_count=0;self.headers={};self.last_diagnostic={}
        self._open()
    def _open(self,storage=None):
        if self.engine=='firefox':self.browser=self.manager.firefox.launch(headless=True)
        elif self.engine=='chromium':self.browser=self.manager.chromium.launch(headless=True,args=[]if self.http2 else['--disable-http2'])
        else:raise ValueError('unsupported_browser_engine')
        options={'locale':'de-DE'}
        if storage is not None:options['storage_state']=storage
        self.context=self.browser.new_context(**options)
        self.context.route('**/*',lambda route:route.abort()if route.request.resource_type in {'image','font','media'}else route.continue_())
    def _network_fault(self):
        self.network_failures=getattr(self,'network_failures',0)+1
        if self.network_failures<2 and getattr(self,'manager',None)is not None:
            # Reset broken network connections while retaining the same cookies,
            # locale, client configuration and IP. Never used on 403/429/challenges.
            try:
                storage=self.context.storage_state()
                self.context.close();self.browser.close();time.sleep(8);self._open(storage);self.restart_count+=1
            except Exception:
                self.network_failures=2
                raise TransportUnavailable('transport_restart_failed')from None
    def get(self,url,timeout=35,**kwargs):
        p=urlsplit(url)
        if p.scheme!='https'or p.hostname!='www.stepstone.de':raise RuntimeError('browser_origin_rejected')
        if getattr(self,'network_failures',0)>=2:raise TransportUnavailable('consecutive_network_failures')
        page_number=parse_qs(p.query).get('page',['1'])[0]
        diag={'kind':'listing'if p.path.startswith('/jobs/')else'detail','page':int(page_number)if page_number.isdigit()and len(page_number)<7 else None,'stage':'navigation','main_status':None}
        self.last_diagnostic=diag;started=time.monotonic()
        page=self.context.new_page()
        try:
            # Do not wait for unrelated scripts/ads to finish parsing. Wait for
            # the main response, then the actual job payload. Its identity and
            # pagination are still validated by the regular parsers afterwards.
            response=page.goto(url,wait_until='commit',timeout=timeout_milliseconds(timeout))
            if response is None:raise RuntimeError('browser_missing_response')
            diag.update(stage='payload',main_status=response.status)
            final=urlsplit(page.url)
            if final.hostname!='www.stepstone.de'or final.scheme!='https':raise RuntimeError('browser_redirect_rejected')
            if response.status==200:
                ready='''() => {
                  const s=window.__PRELOADED_STATE__?.['app-unifiedResultlist'];
                  const r=s?.searchResults;
                  return r && Array.isArray(r.items) && [r.pagination,r.unifiedPagination].some(p=>p && p.page!=null && p.pageCount!=null);
                }'''if p.path.startswith('/jobs/')else'''() => {
                  const job=v=>Array.isArray(v)?v.some(job):!!v && typeof v==='object' &&
                    (((v['@type']==='JobPosting'||Array.isArray(v['@type'])&&v['@type'].includes('JobPosting'))&&typeof v.description==='string'&&v.description.length>=200)||job(v['@graph']));
                  return [...document.querySelectorAll('script[type="application/ld+json"]')].some(n=>{try{return job(JSON.parse(n.textContent))}catch{return false}})||
                    [...document.querySelectorAll('[data-at="job-ad-content"],[data-at="jobad-content"],[data-at="job-ad-main"],[data-at="job-ad-wrapper"]')].some(n=>n.textContent.length>=400);
                }'''
                try:page.wait_for_function(ready,timeout=min(10000,timeout_milliseconds(timeout)))
                except Exception as exc:
                    if type(exc).__name__!='TimeoutError':raise
                    # A missing payload remains a parser/quality failure; never
                    # convert it to a successful empty inventory.
            content=page.content()
            if p.path.startswith('/jobs/') and response.status==200:
                state=page.evaluate('() => window.__PRELOADED_STATE__ && window.__PRELOADED_STATE__["app-unifiedResultlist"]')
                if isinstance(state,dict):content='<script>'+STATE_MARKER+' = '+json.dumps(state,ensure_ascii=False)+';</script>'
            self.network_failures=0
            diag.update(stage='returned',milliseconds=round((time.monotonic()-started)*1000))
            return PageResponse(page.url,content,response.status,dict(response.headers))
        except Exception as exc:
            diag.update(error=type(exc).__name__,milliseconds=round((time.monotonic()-started)*1000))
            if type(exc).__name__=='TimeoutError':
                self._network_fault();raise requests.ReadTimeout('browser_navigation_timeout')from None
            if type(exc).__name__=='Error':
                code=re.search(r'net::ERR_[A-Z0-9_]+',str(exc))
                self._network_fault()
                raise requests.ConnectionError('browser_navigation_error'+(':'+code.group()if code else''))from None
            raise
        finally:
            try:page.close()
            except Exception:pass
    def close(self):
        self.context.close();self.browser.close();self.manager.stop()

def timeout_milliseconds(timeout):
    # requests uses (connect, read), while Playwright accepts one number.
    if isinstance(timeout,(tuple,list)):timeout=timeout[-1]
    return int(float(timeout)*1000)
