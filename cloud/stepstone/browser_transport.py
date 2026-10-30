"""Normal Chromium page navigation for timeout/JS-layout recovery. No stealth.

No proxies, user-agent rotation, CAPTCHA solving or access-control bypass.
Only public StepStone HTTPS pages, images/fonts/media not downloaded.
"""
import json
from dataclasses import dataclass
from kanzlei_discovery.stepstone import STATE_MARKER
from urllib.parse import urlsplit
import requests

@dataclass
class PageResponse:
    url:str
    text:str
    status_code:int
    headers:dict
    def raise_for_status(self):
        if self.status_code>=400:raise requests.HTTPError(f'HTTP {self.status_code}')

class BrowserSession:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.manager=sync_playwright().start();self.browser=self.manager.chromium.launch(headless=True)
        self.context=self.browser.new_context(locale='de-DE');self.headers={}
        self.context.route('**/*',lambda route:route.abort()if route.request.resource_type in {'image','font','media'}else route.continue_())
    def get(self,url,timeout=35,**kwargs):
        p=urlsplit(url)
        if p.scheme!='https'or p.hostname!='www.stepstone.de':raise RuntimeError('browser_origin_rejected')
        page=self.context.new_page()
        try:
            response=page.goto(url,wait_until='domcontentloaded',timeout=timeout*1000)
            if response is None:raise RuntimeError('browser_missing_response')
            final=urlsplit(page.url)
            if final.hostname!='www.stepstone.de'or final.scheme!='https':raise RuntimeError('browser_redirect_rejected')
            content=page.content()
            if p.path.startswith('/jobs/') and response.status==200:
                state=page.evaluate('() => window.__PRELOADED_STATE__ && window.__PRELOADED_STATE__["app-unifiedResultlist"]')
                if isinstance(state,dict):content='<script>'+STATE_MARKER+' = '+json.dumps(state,ensure_ascii=False)+';</script>'
            return PageResponse(page.url,content,response.status,dict(response.headers))
        except Exception as exc:
            if type(exc).__name__=='TimeoutError':raise requests.ReadTimeout('browser_navigation_timeout')from None
            raise
        finally:page.close()
    def close(self):
        self.context.close();self.browser.close();self.manager.stop()
