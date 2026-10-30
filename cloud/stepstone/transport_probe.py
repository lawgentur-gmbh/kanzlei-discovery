"""Bounded public-page diagnostic, no DB and no stored advertisements.

Only normal requests and Chromium. No proxies, stealth or CAPTCHA interaction.
Logs HTTP/network codes and parsed counts, never body/cookies or credentials.
"""
import json,time
import requests
from kanzlei_discovery.stepstone import load_searches,parse_result_state
from browser_transport import BrowserSession
from runtime import ROOT,search_error_code

def probe():
    specs=load_searches(ROOT/'config/stepstone_searches_candidate.json')
    selected=[specs[1],specs[15]]
    outcomes=[]
    for mode in ('requests','chromium'):
        session=requests.Session()if mode=='requests'else BrowserSession()
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

if __name__=='__main__':probe()
