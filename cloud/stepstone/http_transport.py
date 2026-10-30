"""Historical requests transport, bounded to public StepStone HTTPS.

No browser, proxy rotation or altered TLS checks. Session state is retained across
connection renewal. Intended for the controlled Windows cloud comparison.
"""
import time
from urllib.parse import urlsplit,urljoin
import requests
from kanzlei_discovery.stepstone import DEFAULT_HEADERS
from browser_transport import TransportUnavailable

def allowed(url):
    p=urlsplit(url)
    return p.scheme=='https'and p.hostname=='www.stepstone.de'and not p.username and not p.password

class RequestsTransport:
    def __init__(self):
        self.headers=dict(DEFAULT_HEADERS);self.session=requests.Session()
        self.network_failures=0;self.restart_count=0;self.scope_restarts=0;self.last_diagnostic={}
    def _renew(self):
        cookies=self.session.cookies.copy();self.session.close()
        self.session=requests.Session();self.session.cookies.update(cookies)
    def get(self,url,timeout=90,**kwargs):
        if not allowed(url):raise ValueError('http_origin_rejected')
        if self.network_failures>=2:raise TransportUnavailable('consecutive_network_failures')
        kwargs.pop('allow_redirects',None)
        self.session.headers.update(self.headers);started=time.monotonic()
        self.last_diagnostic={'stage':'http_request','main_status':None}
        try:
            response=self.session.get(url,timeout=timeout,allow_redirects=False,**kwargs)
            for _ in range(3):
                if response.status_code not in (301,302,303,307,308):break
                target=urljoin(response.url,response.headers.get('location',''))
                if not allowed(target):raise ValueError('http_redirect_rejected')
                response=self.session.get(target,timeout=timeout,allow_redirects=False,**kwargs)
            self.network_failures=0
            self.last_diagnostic.update(stage='returned',main_status=response.status_code,milliseconds=round((time.monotonic()-started)*1000))
            return response
        except (requests.Timeout,requests.ConnectionError)as exc:
            self.network_failures+=1
            self.last_diagnostic.update(error=type(exc).__name__,milliseconds=round((time.monotonic()-started)*1000))
            if self.network_failures==1:
                self._renew();self.restart_count+=1;time.sleep(8)
            raise
    def next_search(self):
        if self.network_failures:raise TransportUnavailable('unhealthy_transport_at_scope_boundary')
        self._renew();self.scope_restarts+=1
    def close(self):self.session.close()
