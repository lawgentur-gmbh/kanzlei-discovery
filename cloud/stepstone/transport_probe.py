"""Bounded public-page diagnostic, no DB and no stored advertisements.

Only normal requests and Chromium. No proxies, stealth or CAPTCHA interaction.
Logs HTTP/network codes and parsed counts, never body/cookies or credentials.
"""
import argparse,json,time
import requests
from kanzlei_discovery.stepstone import load_searches,parse_result_state
from browser_transport import BrowserSession
from runtime import ROOT,search_error_code

def probe(http1=False):
    specs=load_searches(ROOT/'config/stepstone_searches_candidate.json')
    selected=[specs[1],specs[15]]
    outcomes=[]
    for mode in (('chromium_http1',)if http1 else('requests','chromium')):
        session=requests.Session()if mode=='requests'else BrowserSession(http2=mode!='chromium_http1')
        try:
            for spec in selected:
                started=time.monotonic();item={'transport':mode,'search':spec.name}
                try:
                    response=session.get(spec.url,timeout=20)
                    item['status']=response.status_code
                    if response.status_code==200:
                        try:
                            state=parse_result_state(response.text)
                            item['rows']=len((state.get('searchResults')or{}).get('items')or[])
                        except Exception as exc:item['error']=search_error_code(exc)
                except Exception as exc:item['error']=search_error_code(exc)
                item['seconds']=round(time.monotonic()-started,2)
                outcomes.append(item);print('TRANSPORT_DIAG',json.dumps(item,ensure_ascii=False),flush=True)
                time.sleep(2)
        finally:session.close()
    return outcomes

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--http1',action='store_true');probe(p.parse_args().http1)
