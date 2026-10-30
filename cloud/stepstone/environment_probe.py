"""One public base-search request to classify cloud connection failures.

No DB, retries, cookies, private data output or original-research side effects.
Only safe exception class names/errno, library versions and status/counts logged.
"""
import json,os,ssl,time
import requests,urllib3
from pathlib import Path
from kanzlei_discovery.stepstone import load_searches,DEFAULT_HEADERS,parse_result_state

def types(exc):
    queue=[exc];seen=set();out=[]
    while queue and len(out)<12:
        value=queue.pop(0)
        if not isinstance(value,BaseException)or id(value)in seen:continue
        seen.add(id(value));item={'type':type(value).__name__}
        errno=getattr(value,'errno',None)
        if isinstance(errno,int):item['errno']=errno
        out.append(item)
        queue.extend([value.__cause__,value.__context__,getattr(value,'reason',None)])
        queue.extend(a for a in value.args if isinstance(a,BaseException))
    return out

def main():
    root=Path(__file__).resolve().parents[2]
    spec=load_searches(root/'config/stepstone_searches_base.json')[0]
    result={'request_budget':1,'runner_os':os.getenv('RUNNER_OS','local'),'requests':requests.__version__,'urllib3':urllib3.__version__,'ssl':ssl.OPENSSL_VERSION,'proxy_environment_present':any(os.getenv(k)for k in ['HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy'])}
    started=time.monotonic()
    try:
        with requests.Session()as session:
            session.headers.update(DEFAULT_HEADERS)
            response=session.get(spec.url,timeout=(15,30),allow_redirects=False)
            result['http_status']=response.status_code
            if response.status_code==200:
                state=parse_result_state(response.text)
                result['rows']=len((state.get('searchResults')or{}).get('items')or[])
    except Exception as exc:result['error_types']=types(exc)
    result['seconds']=round(time.monotonic()-started,2)
    print('ENVIRONMENT_PROBE',json.dumps(result),flush=True)

if __name__=='__main__':main()
