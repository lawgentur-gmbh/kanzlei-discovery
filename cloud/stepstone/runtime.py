"""Finite weekly StepStone acquisition. Private archive, never STEREJO jobs.

Only bounded, tested parser alternatives/retries are automatic. No generated code,
filter removal, proxy rotation, CAPTCHA bypass, reverse research, or publication.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,statistics,sys,time,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlsplit
import requests
from bs4 import BeautifulSoup
import psycopg
from psycopg.types.json import Jsonb
from kanzlei_discovery.stepstone import StepstoneClient,load_searches,search_fingerprint,merge_search_rows,DEFAULT_HEADERS

VERSION='stepstone-internal-2'
ROOT=Path(__file__).resolve().parents[2]
POLICY=json.loads((Path(__file__).parent/'policy.json').read_text())

def now():return datetime.now(timezone.utc)
def normalize(value):return re.sub(r'\s+',' ',str(value or '')).strip()
def digest(value):return hashlib.sha256(value.encode('utf8')).hexdigest()
def portal_url(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.hostname=='www.stepstone.de' and not p.username and not p.password
def job_id(url):
    m=re.search(r'--(\d+)(?:-inline)?\.html',urlsplit(url).path)
    return m.group(1)if m else None
def plain(html):
    soup=BeautifulSoup(html or '', 'html.parser')
    for x in soup.select('script,style,nav,footer,header,form'):x.decompose()
    return '\n'.join(x for line in soup.get_text('\n',strip=True).splitlines()if(x:=normalize(line)))
def json_jobs(value):
    if isinstance(value,list):
        for x in value:yield from json_jobs(x)
    elif isinstance(value,dict):
        if 'JobPosting' in ([value.get('@type')]if isinstance(value.get('@type'),str)else(value.get('@type')or [])):yield value
        if '@graph'in value:yield from json_jobs(value['@graph'])

def extract_detail(html,url,expected_id):
    """Accept a complete job-bound body, never the result snippet or whole page."""
    soup=BeautifulSoup(html,'html.parser')
    if re.search(r'captcha|access denied|verify you are human',soup.title.get_text()if soup.title else '',re.I):
        return {'quality':'blocked','text':None}
    for script in soup.select('script[type="application/ld+json"]'):
        try:payload=json.loads(script.string or script.get_text())
        except ValueError:continue
        for job in json_jobs(payload):
            identifier=job.get('identifier')or ''
            identifier=identifier.get('value','')if isinstance(identifier,dict)else str(identifier)
            schema_url=job.get('url')or ''
            # A different native ID or third-party detail must never be attached.
            if schema_url and (not portal_url(schema_url)or job_id(schema_url)!=expected_id):continue
            if identifier and re.fullmatch(r'\d+',str(identifier))and str(identifier)!=expected_id:continue
            if job_id(url)!=expected_id:continue
            text=plain(job.get('description',''))
            if len(text)>=200 and len(text.split())>=30:
                return {'quality':'fulltext','text':text,'method':'jobposting',
                    'title':normalize(job.get('title')),'published_at':job.get('datePosted'),
                    'valid_through':job.get('validThrough')}
    # Public job-ad section layouts, excluding generic article/navigation text.
    sections=[]
    for selector in ('[data-at="job-ad-content"]','[data-at="jobad-content"]','[data-at="job-ad-main"]','[data-at="job-ad-wrapper"]'):
        for node in soup.select(selector):
            text=plain(str(node))
            if len(text)>=400 and len(text.split())>=60:sections.append(text)
        if sections:break
    if sections and job_id(url)==expected_id:
        return {'quality':'fulltext','text':'\n\n'.join(dict.fromkeys(sections)),'method':'job_ad_section'}
    return {'quality':'missing_fulltext','text':None}

class DetailClient:
    def __init__(self,browser=None):
        self.session=browser or requests.Session();self.session.headers.update(DEFAULT_HEADERS);self.last=0
    def get(self,row,cached):
        url=row['Titel_url'];native=row['Stepstone_ID']or job_id(url)
        if not native or not portal_url(url)or job_id(url)!=native:return {'quality':'identity_error','text':None}
        headers={}
        if cached:
            if cached.get('etag'):headers['If-None-Match']=cached['etag']
            if cached.get('last_modified'):headers['If-Modified-Since']=cached['last_modified']
        for attempt in range(2):
            time.sleep(max(0,POLICY['http_delay_seconds']-(time.monotonic()-self.last)))
            self.last=time.monotonic()
            try:
                # No automatic cross-domain redirects / employer visits.
                response=self.session.get(url,headers=headers,timeout=(10,30),allow_redirects=False)
                for _ in range(3):
                    if response.status_code not in (301,302,303,307,308):break
                    from urllib.parse import urljoin
                    target=urljoin(response.url,response.headers.get('location',''))
                    if not portal_url(target)or job_id(target)!=native:return {'quality':'unexpected_redirect','text':None}
                    response=self.session.get(target,headers=headers,timeout=(10,30),allow_redirects=False)
                if response.status_code==304 and cached and cached.get('has_fulltext'):
                    return {'quality':'not_modified','text':None}
                if response.status_code in (404,410):return {'quality':'closed_http','text':None}
                if response.status_code in (403,429):return {'quality':'blocked_or_rate_limited','text':None}
                if response.status_code>=500 and attempt==0:time.sleep(5);continue
                if response.status_code!=200:return {'quality':'http_error','text':None}
                if 'html'not in response.headers.get('content-type','').lower():return {'quality':'unexpected_content_type','text':None}
                result=extract_detail(response.text,response.url,native)
                result.update(etag=response.headers.get('etag'),last_modified=response.headers.get('last-modified'))
                return result
            except requests.RequestException:
                if attempt==0:time.sleep(3)
        return {'quality':'network_error','text':None}

def anomalous(count,history):
    if not history:return []
    baseline=statistics.median(history)
    if count==0 and baseline>0:return ['unexpected_zero_inventory']
    delta=abs(count-baseline)
    return [f'count={count}; baseline={baseline:g}']if delta>=POLICY['absolute_count_threshold']and delta/max(baseline,1)>POLICY['relative_count_threshold']else []

def search_error_code(exc):
    chain=[];current=exc
    while current is not None and len(chain)<4:
        chain.append(type(current).__name__)
        code=re.search(r'net::ERR_[A-Z0-9_]+',str(current))
        if code:chain.append(code.group().replace('net::',''))
        current=current.__cause__
    return 'search_'+'_'.join(chain)

class Store:
    def __init__(self,conn):self.conn=conn
    def history(self,key,fingerprint):
        return [x[0]for x in self.conn.execute('select distinct_count from kr_portals.search_runs where search_key=%s and fingerprint=%s and healthy order by finished_at desc limit 4',(key,fingerprint)).fetchall()]
    def cached(self):
        with self.conn.cursor(row_factory=psycopg.rows.dict_row)as c:
            c.execute("select external_id,etag,last_modified,last_detail_check,text_hash is not null as has_fulltext from kr_portals.jobs where provider='stepstone'")
            return {r['external_id']:r for r in c.fetchall()}
    def save_job(self,row,detail,run_id):
        native=row['Stepstone_ID']or job_id(row['Titel_url'])
        if not native:raise ValueError('missing_native_id')
        body=detail.get('text');text_hash=digest(body)if body else None
        metadata={k:row.get(k,'')for k in ['Job_Titel','Name_des_Unternehmens','Standort','Erscheinen','Home_möglich']}
        metadata['source_url']=row['Titel_url']
        metadata_hash=digest(json.dumps(metadata,sort_keys=True,ensure_ascii=False))
        dedupe_key=digest('|'.join(normalize(row.get(k)).casefold()for k in ('Name_des_Unternehmens','Job_Titel','Standort'))+'|'+text_hash)if text_hash and row.get('Name_des_Unternehmens')else 'id:'+native
        with self.conn.transaction():
            if text_hash:self.conn.execute('insert into kr_portals.texts(hash,body)values(%s,%s)on conflict(hash)do nothing',(text_hash,body))
            self.conn.execute('''insert into kr_portals.jobs(provider,external_id,source_url,title,employer,location,published_raw,metadata_hash,text_hash,dedupe_key,first_seen,last_seen,last_detail_check,detail_quality,etag,last_modified)
                values('stepstone',%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now(),now(),%s,%s,%s)
                on conflict(provider,external_id)do update set source_url=excluded.source_url,title=excluded.title,employer=excluded.employer,location=excluded.location,published_raw=excluded.published_raw,
                metadata_hash=excluded.metadata_hash,text_hash=coalesce(excluded.text_hash,kr_portals.jobs.text_hash),dedupe_key=case when excluded.text_hash is not null then excluded.dedupe_key else kr_portals.jobs.dedupe_key end,last_seen=now(),last_detail_check=case when excluded.detail_quality='cached_fresh' then kr_portals.jobs.last_detail_check else now()end,
                detail_quality=excluded.detail_quality,etag=coalesce(excluded.etag,kr_portals.jobs.etag),last_modified=coalesce(excluded.last_modified,kr_portals.jobs.last_modified)''',
                (native,row['Titel_url'],row['Job_Titel'],row['Name_des_Unternehmens'],row['Standort'],row['Erscheinen'],metadata_hash,text_hash,dedupe_key,detail['quality'],detail.get('etag'),detail.get('last_modified')))
            # Repeated identical states update tiny counters; no repeated body copy.
            current_hash=self.conn.execute("select text_hash from kr_portals.jobs where provider='stepstone' and external_id=%s",(native,)).fetchone()[0]
            state_hash=digest(metadata_hash+':'+(current_hash or''))
            self.conn.execute('''insert into kr_portals.versions(provider,external_id,state_hash,metadata,text_hash,first_seen,last_seen,seen_count)
                values('stepstone',%s,%s,%s,%s,now(),now(),1)on conflict(provider,external_id,state_hash)do update set last_seen=now(),seen_count=kr_portals.versions.seen_count+1''',(native,state_hash,Jsonb(metadata),current_hash))
    def touch(self,run_id,status,summary):
        self.conn.execute('update kr_portals.runs set status=%s,summary=%s,finished_at=now() where id=%s',(status,Jsonb(summary),run_id))

def notify(summary,test=False):
    """One assigned GitHub notification per persistent weekly incident."""
    token=os.environ.get('GITHUB_TOKEN');repo=os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:raise RuntimeError('alert_channel_not_configured')
    api=f'https://api.github.com/repos/{repo}/issues'
    headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    week=now().strftime('%G-W%V');title=(f'StepStone-Archiv: Benachrichtigungstest ({week})'if test else f'StepStone-Prüfung erforderlich ({week})')
    # Public repository: counts/error codes only, no ads, texts, secret values or personal data.
    intro='Einmaliger Einrichtungstest des Warnwegs; keine Störung gemeldet.'if test else'Der interne Sonntagslauf benötigt Prüfung. Nach begrenztem Wiederholungsversuch bleiben Abweichungen bestehen.'
    body=intro+'\n\n```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n\nKeine Stellen veröffentlicht oder gelöscht. Workflow: '+os.environ.get('GITHUB_SERVER_URL','https://github.com')+'/'+repo+'/actions/runs/'+os.environ.get('GITHUB_RUN_ID','')
    r=requests.get(api,params={'state':'open','per_page':100},headers=headers,timeout=20);r.raise_for_status()
    existing=next((i for i in r.json()if i.get('title')==title),None)
    if existing and summary.get('issues')==['cloud_workflow_failed_or_timed_out']:
        print('ALERT_ALREADY_RECORDED',existing['html_url'])
        return
    if existing:
        response=requests.patch(existing['url'],headers=headers,json={'body':body},timeout=20)
    else:
        response=requests.post(api,headers=headers,json={'title':title,'body':body,'assignees':['Lawgentur']},timeout=20)
    response.raise_for_status()
    print('ALERT_CREATED',response.json()['html_url'])

def sample_rows(rows,per_search=None):
    return rows if per_search is None else rows[:per_search]

def run(limit=None,max_details=None,sample_per_search=None,sample_pages=2,pagination_pilot=False):
    if pagination_pilot:max_details=3
    limited=bool(limit or max_details or sample_per_search)
    searches=load_searches(ROOT/('config/stepstone_searches_candidate.json'if sample_per_search else'config/stepstone_searches.json'))
    if pagination_pilot:searches=[next(s for s in searches if s.name=='Notarfachangestellter')]
    if limit:searches=searches[:limit]
    fingerprint=search_fingerprint(searches);run_id=str(uuid.uuid4())
    conn=psycopg.connect(os.environ['STEPSTONE_DATABASE_URL'],autocommit=True,connect_timeout=20,application_name='stepstone-private-weekly',sslmode='verify-full',sslrootcert=str(Path(__file__).parent/'supabase-ca.crt'))
    store=Store(conn)
    if not conn.execute("select pg_try_advisory_lock(713015,1)").fetchone()[0]:raise RuntimeError('another_stepstone_run_is_active')
    conn.execute('insert into kr_portals.runs(id,status,parser_version,fingerprint,summary)values(%s,%s,%s,%s,%s)',(run_id,'running',VERSION,fingerprint,Jsonb({'limited':limited})))
    all_rows={};query_summary=[];issues=[];browser=None
    try:
        # Cloud comparison: ordinary HTTP timed out; default Chromium failed on
        # its second navigation. HTTP/1.1 Chromium passed both in the same session.
        from browser_transport import BrowserSession
        browser=BrowserSession(http2=False)
        for spec in searches:
            key=digest(spec.name);history=store.history(key,fingerprint);chosen=None;errors=[];partial_rows={}
            for attempt in range(POLICY['search_attempts']):
                try:
                    client=StepstoneClient(session=browser,delay_seconds=1,timeout_seconds=35,retries=2)
                    result=client.fetch_search(spec,now().date().isoformat(),max_pages=None if pagination_pilot else sample_pages if sample_per_search else 1 if max_details else None,
                        on_page_rows=lambda rows:merge_search_rows(partial_rows,rows))
                    errors=anomalous(len(result.rows),history)
                    if not result.inventory_complete:
                        ignored={'explicit_page_limit','unique_inventory_differs_from_declared_total'}if(max_details or sample_per_search)and not pagination_pilot else set()
                        errors+=[x for x in result.coverage_warnings if x not in ignored]
                    chosen=result
                    if not errors:break
                except Exception as exc:
                    errors=[search_error_code(exc)]
                    print('SEARCH_DIAGNOSTIC',json.dumps({'search':spec.name,'attempt':attempt+1,'errors':errors}),flush=True)
                    if browser is None and 'ReadTimeout'in errors[0]and attempt+1<POLICY['search_attempts']:
                        from browser_transport import BrowserSession
                        browser=BrowserSession()
                        print('RECOVERY normal_browser_navigation',flush=True)
                if attempt+1<POLICY['search_attempts']:time.sleep(10)
            count=len(chosen.rows)if chosen else len(partial_rows)
            if not history and count==0:errors.append('unexpected_initial_zero')
            healthy=not errors and chosen is not None and not limited
            conn.execute('insert into kr_portals.search_runs(run_id,search_key,search_name,fingerprint,distinct_count,healthy,diagnostics)values(%s,%s,%s,%s,%s,%s,%s)',(run_id,key,spec.name,fingerprint,count,healthy,Jsonb({'errors':errors})))
            query_summary.append({'name':spec.name,'count':count,'healthy':healthy,'errors':errors,'sample_only':bool(sample_per_search),'pages':chosen.pages if chosen else None,'scope_complete':chosen.inventory_complete if chosen else False})
            if errors:issues.append('search_quality')
            if chosen:merge_search_rows(all_rows,sample_rows(chosen.rows,sample_per_search))
            else:merge_search_rows(all_rows,sample_rows(list(partial_rows.values()),sample_per_search))
            print('SEARCH_RESULT',json.dumps(query_summary[-1],ensure_ascii=False),flush=True)
            store.touch(run_id,'running',{'searches':query_summary,'distinct_jobs':len(all_rows)})
        cache=store.cached();details=DetailClient(browser);quality={};processed=0
        for row in all_rows.values():
            if max_details is not None and processed>=max_details:break
            native=row['Stepstone_ID']or job_id(row['Titel_url']);old=cache.get(native)
            fresh=old and old['has_fulltext']and old['last_detail_check']and old['last_detail_check']>now()-timedelta(days=POLICY['detail_refresh_days'])
            detail={'quality':'cached_fresh','text':None}if fresh else details.get(row,old)
            store.save_job(row,detail,run_id);processed+=1
            quality[detail['quality']]=quality.get(detail['quality'],0)+1
            if processed%50==0:
                store.touch(run_id,'running',{'searches':query_summary,'distinct_jobs':len(all_rows),'processed':processed,'details':quality})
                print('DETAIL_PROGRESS',processed,'/',len(all_rows),flush=True)
        good=sum(quality.get(k,0)for k in ('fulltext','not_modified','cached_fresh'))
        if processed==0 or good/max(processed,1)<POLICY['minimum_fulltext_fraction']:issues.append('fulltext_coverage')
        status='pilot'if limited else'warning'if issues else'complete'
        summary={'distinct_jobs':len(all_rows),'processed':processed,'details':quality,'searches':query_summary,'issues':sorted(set(issues)),'limited':limited}
        store.touch(run_id,status,summary)
        print('RUN_RESULT',json.dumps(summary,ensure_ascii=False),flush=True)
        if issues and not limited:notify(summary)
        return 0 if not issues else 2
    except Exception as exc:
        # Do not log connection strings, response bodies, SQL parameter values, or tokens.
        summary={'issues':['runtime_'+type(exc).__name__]}
        try:store.touch(run_id,'failed',summary)
        finally:
            if not limited:notify(summary)
        raise RuntimeError(type(exc).__name__)from None
    finally:
        if browser:browser.close()
        conn.close()

def network_probe():
    """Small cloud preflight, no DB, stored text, reverse search or publication."""
    spec=load_searches(ROOT/'config/stepstone_searches.json')[0]
    from browser_transport import BrowserSession
    browser=BrowserSession(http2=False)
    try:
        client=StepstoneClient(session=browser,delay_seconds=1,timeout_seconds=25,retries=2)
        result=client.fetch_search(spec,now().date().isoformat(),max_pages=1)
        details=DetailClient(browser);counts={}
        for row in result.rows[:3]:
            detail=details.get(row,None)
            counts[detail['quality']]=counts.get(detail['quality'],0)+1
    finally:browser.close()
    print('PREFLIGHT',json.dumps({'sample_rows':len(result.rows),'details':counts,'database_changed':False}))
    return 0 if counts.get('fulltext')else 2

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--limit-searches',type=int);p.add_argument('--max-details',type=int);p.add_argument('--sample-per-search',type=int);p.add_argument('--sample-pages',type=int,default=2);p.add_argument('--pagination-pilot',action='store_true');p.add_argument('--network-probe',action='store_true')
    a=p.parse_args()
    if any(x is not None and x<1 for x in (a.limit_searches,a.max_details,a.sample_per_search,a.sample_pages)):p.error('limits must be positive')
    if a.pagination_pilot and a.sample_per_search:p.error('pagination pilot and survey are separate modes')
    sys.exit(network_probe()if a.network_probe else run(a.limit_searches,a.max_details,a.sample_per_search,a.sample_pages,a.pagination_pilot))
