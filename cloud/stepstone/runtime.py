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

VERSION='stepstone-internal-3'
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
        code=re.search(r'net::ERR_[A-Z0-9_]+|NS_ERROR_[A-Z0-9_]+|SSL_ERROR_[A-Z0-9_]+',str(current))
        if code:chain.append(code.group().replace('net::',''))
        current=current.__cause__
    return 'search_'+'_'.join(chain)

class Store:
    def __init__(self,conn):self.conn=conn
    def checkpoint_page(self,rows,run_id,key,name,fingerprint,page_responses):
        payload=[]
        fields=('Job_Titel','Titel_url','Name_des_Unternehmens','Standort','Erscheinen','Home_möglich','Stepstone_ID','Search_Query')
        for row in rows:
            native=row.get('Stepstone_ID')or job_id(row.get('Titel_url',''))
            if not native or not portal_url(row.get('Titel_url',''))or job_id(row['Titel_url'])!=native:
                raise PersistenceError('candidate_identity_rejected')
            payload.append({'external_id':native,'source_url':row['Titel_url'],'row_data':{k:row.get(k,'')for k in fields}})
        try:
            with self.conn.transaction():
                if payload:
                    self.conn.execute('''insert into kr_portals.stepstone_candidates(external_id,source_url,row_data,search_keys,last_run_id)
                      select x.external_id,x.source_url,x.row_data,array[%s]::text[],%s::uuid
                      from jsonb_to_recordset(%s::jsonb) as x(external_id text,source_url text,row_data jsonb)
                      on conflict(external_id)do update set source_url=excluded.source_url,row_data=excluded.row_data,last_seen=now(),last_run_id=excluded.last_run_id,
                        search_keys=array(select distinct unnest(kr_portals.stepstone_candidates.search_keys||excluded.search_keys)order by 1)''',(key,run_id,Jsonb(payload)))
                count=self.conn.execute('select count(*)from kr_portals.stepstone_candidates where last_run_id=%s and search_keys @> array[%s]::text[]',(run_id,key)).fetchone()[0]
                self.search_checkpoint(run_id,key,name,fingerprint,count,False,{'phase':'listing','page_responses':page_responses,'persisted_ids':count})
        except Exception as exc:raise PersistenceError(type(exc).__name__)from None
        return count
    def search_checkpoint(self,run_id,key,name,fingerprint,count,healthy,diagnostics):
        self.conn.execute('''insert into kr_portals.search_runs(run_id,search_key,search_name,fingerprint,distinct_count,healthy,diagnostics)
          values(%s,%s,%s,%s,%s,%s,%s)on conflict(run_id,search_key)do update set distinct_count=excluded.distinct_count,healthy=excluded.healthy,diagnostics=excluded.diagnostics,finished_at=now()''',
          (run_id,key,name,fingerprint,count,healthy,Jsonb(diagnostics)))
    def queued(self,key,run_id,resume=False):
        rows=self.conn.execute('''select row_data from kr_portals.stepstone_candidates
          where search_keys @> array[%s]::text[] and (last_run_id=%s or %s)
          order by case when detail_status in ('pending','network_error','missing_fulltext')then 0 else 1 end,first_seen,external_id''',(key,run_id,resume)).fetchall()
        return [row[0]for row in rows]
    def history(self,key,fingerprint):
        return [x[0]for x in self.conn.execute('select distinct_count from kr_portals.search_runs where search_key=%s and fingerprint=%s and healthy order by finished_at desc limit 4',(key,fingerprint)).fetchall()]
    def cached(self):
        with self.conn.cursor(row_factory=psycopg.rows.dict_row)as c:
            c.execute("select external_id,etag,last_modified,last_detail_check,detail_quality,text_hash is not null as has_fulltext from kr_portals.jobs where provider='stepstone'")
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
            self.conn.execute('update kr_portals.stepstone_candidates set detail_status=%s,detail_checked_at=now()where external_id=%s',(detail['quality'],native))
    def touch(self,run_id,status,summary):
        self.conn.execute("update kr_portals.runs set status=%s,summary=%s,finished_at=case when %s='running' then null else now()end where id=%s",(status,Jsonb(summary),status,run_id))

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

class TimeBudgetReached(RuntimeError):pass
class PersistenceError(RuntimeError):pass

def capture_page(target,rows,deadline):
    merge_search_rows(target,rows)
    if time.monotonic()>=deadline:raise TimeBudgetReached('run_time_budget_reached')

def detail_priority(row,cache):
    old=cache.get(row.get('Stepstone_ID')or job_id(row['Titel_url']))or{}
    has_text=bool(old.get('has_fulltext'))
    good=old.get('detail_quality')in {'fulltext','not_modified','cached_fresh'}
    return (has_text and good,has_text,old.get('last_detail_check')or datetime.min.replace(tzinfo=timezone.utc))

def run(limit=None,max_details=None,sample_per_search=None,sample_pages=2,pagination_pilot=False,repair=False,resume_only=False,browser_engine=None):
    from incremental_runtime import run as incremental_run
    return incremental_run(sys.modules[__name__],limit,max_details,sample_per_search,sample_pages,pagination_pilot,repair,resume_only,browser_engine)


def network_probe():
    """Small cloud preflight, no DB, stored text, reverse search or publication."""
    spec=load_searches(ROOT/'config/stepstone_searches.json')[0]
    from browser_transport import BrowserSession
    browser=BrowserSession(http2=False,engine=POLICY.get('browser_engine','chromium'))
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
    p=argparse.ArgumentParser();p.add_argument('--limit-searches',type=int);p.add_argument('--max-details',type=int);p.add_argument('--sample-per-search',type=int);p.add_argument('--sample-pages',type=int,default=2);p.add_argument('--pagination-pilot',action='store_true');p.add_argument('--network-probe',action='store_true');p.add_argument('--repair-small',action='store_true');p.add_argument('--resume-only',action='store_true')
    p.add_argument('--browser-engine',choices=['chromium','firefox','requests'])
    a=p.parse_args()
    if any(x is not None and x<1 for x in (a.limit_searches,a.max_details,a.sample_per_search,a.sample_pages)):p.error('limits must be positive')
    if a.pagination_pilot and a.sample_per_search:p.error('pagination pilot and survey are separate modes')
    sys.exit(network_probe()if a.network_probe else run(a.limit_searches,a.max_details,a.sample_per_search,a.sample_pages,a.pagination_pilot,a.repair_small,a.resume_only,a.browser_engine))
