"""Durable page checkpoints and per-search detail processing. StepStone only."""
from browser_transport import BrowserSession

GOOD={'fulltext','cached_fresh','not_modified'}

def run(r,limit=None,max_details=None,sample_per_search=None,sample_pages=2,pagination_pilot=False,repair=False,resume_only=False,browser_engine=None):
    browser_engine=browser_engine or r.POLICY.get('browser_engine','chromium')
    if repair and sample_per_search is None:sample_per_search=2
    if resume_only:sample_per_search=None
    if pagination_pilot:max_details=3
    limited=bool(limit or max_details or sample_per_search or resume_only or repair)
    if not limited and not r.POLICY.get('large_run_enabled',False):
        summary={'issues':['large_run_not_validated'],'action':'Small cloud acceptance tests required before full capture. No crawl started.'}
        print('FULL_RUN_GATED',r.json.dumps(summary),flush=True)
        r.notify(summary)
        return 2
    specs=r.load_searches(r.ROOT/'config/stepstone_searches.json')
    if repair:
        names={'Rechtsanwaltsfachangestellter','Notarfachangestellter','Rechtsanwalt/Rechtsanwältin','Legal Counsel','Unternehmensjurist/in','Syndikusrechtsanwalt'}
        specs=[s for s in specs if s.name in names]
    if pagination_pilot:specs=[next(s for s in specs if s.name=='Notarfachangestellter')]
    if limit:specs=specs[:limit]
    fingerprint=r.search_fingerprint(specs);run_id=str(r.uuid.uuid4())
    conn=r.psycopg.connect(r.os.environ['STEPSTONE_DATABASE_URL'],autocommit=True,connect_timeout=20,application_name='stepstone-private-weekly',sslmode='verify-full',sslrootcert=str(r.Path(__file__).parent/'supabase-ca.crt'))
    store=r.Store(conn);browser=None;seen={};done=set();queries=[];issues=[];quality={}
    deadline=r.time.monotonic()+r.POLICY['maximum_run_seconds']
    def snapshot():
        return {'searches':queries,'distinct_jobs':len(seen),'processed':len(done),'details':quality,
                'issues':sorted(set(issues)),'limited':limited,'resume_only':resume_only,
                'unprocessed_searches':len(specs)-len(queries),'transport_restarts':getattr(browser,'restart_count',0),
                'persisted_checkpoints':True,'runner_os':r.os.environ.get('RUNNER_OS','local'),'browser_engine':browser_engine,'scope_session_restarts':getattr(browser,'scope_restarts',0),'last_transport_diagnostic':getattr(browser,'last_diagnostic',{})}
    if not conn.execute('select pg_try_advisory_lock(713015,1)').fetchone()[0]:
        conn.close();raise RuntimeError('another_stepstone_run_is_active')
    conn.execute('insert into kr_portals.runs(id,status,parser_version,fingerprint,summary)values(%s,%s,%s,%s,%s)',(run_id,'running',r.VERSION,fingerprint,r.Jsonb(snapshot())))
    try:
        if browser_engine=='requests':
            from http_transport import RequestsTransport
            browser=RequestsTransport()
        else:browser=BrowserSession(http2=False,engine=browser_engine)
        cache=store.cached()
        for spec in specs:
            if r.time.monotonic()>=deadline:issues.append('run_time_budget_reached');break
            key=r.digest(spec.name);partial={};chosen=None;errors=[];pages=0
            def receive(rows):
                nonlocal pages
                pages+=1
                store.checkpoint_page(rows,run_id,key,spec.name,fingerprint,pages)
                r.merge_search_rows(seen,rows)
                r.capture_page(partial,rows,deadline)
                if pages%5==0:
                    progress=snapshot();progress['current_search']={'name':spec.name,'page_responses':pages,'persisted_ids':len(partial)}
                    store.touch(run_id,'running',progress)
            if not resume_only:
                for attempt in range(r.POLICY['search_attempts']):
                    try:
                        client=r.StepstoneClient(session=browser,delay_seconds=r.POLICY['http_delay_seconds'],timeout_seconds=90 if browser_engine=='requests'else 25,retries=2)
                        chosen=client.fetch_search(spec,r.now().date().isoformat(),max_pages=None if pagination_pilot else sample_pages if sample_per_search else 1 if max_details else None,on_page_rows=receive)
                        errors=r.anomalous(len(chosen.rows),store.history(key,fingerprint))
                        ignored={'explicit_page_limit','unique_inventory_differs_from_declared_total'}if(sample_per_search or max_details)and not pagination_pilot else set()
                        errors += [e for e in chosen.coverage_warnings if e not in ignored]
                        if not errors:break
                    except r.PersistenceError:raise
                    except r.TimeBudgetReached:
                        errors=['run_time_budget_reached'];issues.extend(errors);break
                    except Exception as exc:
                        errors=[r.search_error_code(exc)]
                        print('SEARCH_DIAGNOSTIC',r.json.dumps({'search':spec.name,'attempt':attempt+1,'errors':errors}),flush=True)
                        if getattr(browser,'network_failures',0)>=2:
                            errors.append('transport_unavailable');issues.append('transport_unavailable');break
                    if attempt+1<r.POLICY['search_attempts']:r.time.sleep(r.POLICY['search_retry_delay_seconds'])
            queued=store.queued(key,run_id,resume=resume_only)
            if resume_only:r.merge_search_rows(seen,queued)
            count=len(chosen.rows)if chosen else len(partial)if not resume_only else len(queued)
            if not resume_only and count==0 and not(chosen and chosen.inventory_complete):errors.append('unverified_empty_inventory')
            q={'name':spec.name,'count':count,'errors':errors,'healthy':not errors and chosen is not None and not limited,
               'pages':chosen.pages if chosen else pages,'scope_complete':chosen.inventory_complete if chosen else False,
               'declared_total':chosen.declared_total if chosen else None,'offered_pages':chosen.offered_pages if chosen else None,
               'persisted_candidates':len(queued),'details_processed':0,'detail_quality':{}}
            store.search_checkpoint(run_id,key,spec.name,fingerprint,count,q['healthy'],{'phase':'listing_finished','errors':errors,'persisted_ids':len(queued),'page_responses':pages})
            if errors:issues.append('search_quality')
            queries.append(q)
            # Details are committed now, before the next search can fail.
            eligible=[row for row in queued if (row.get('Stepstone_ID')or r.job_id(row['Titel_url']))not in done]
            eligible=sorted(eligible,key=lambda row:r.detail_priority(row,cache))
            if sample_per_search:eligible=eligible[:sample_per_search]
            details=r.DetailClient(browser)
            for row in eligible:
                if getattr(browser,'network_failures',0)>=2:
                    issues.append('transport_unavailable');break
                if max_details and len(done)>=max_details:break
                if r.time.monotonic()>=deadline:issues.append('run_time_budget_reached');break
                native=row.get('Stepstone_ID')or r.job_id(row['Titel_url']);old=cache.get(native)
                fresh=old and old['has_fulltext']and old.get('detail_quality')in GOOD and old['last_detail_check']and old['last_detail_check']>r.now()-r.timedelta(days=r.POLICY['detail_refresh_days'])
                detail={'quality':'cached_fresh','text':None}if fresh else details.get(row,old)
                store.save_job(row,detail,run_id);done.add(native)
                label=detail['quality'];quality[label]=quality.get(label,0)+1
                q['details_processed']+=1;q['detail_quality'][label]=q['detail_quality'].get(label,0)+1
                if len(done)%10==0:store.touch(run_id,'running',snapshot())
            cache=store.cached()
            if getattr(browser,'network_failures',0)>=2:issues.append('transport_unavailable')
            store.search_checkpoint(run_id,key,spec.name,fingerprint,count,q['healthy'],{'phase':'search_committed','errors':errors,'persisted_ids':len(queued),'details_processed':q['details_processed'],'detail_quality':q['detail_quality']})
            store.touch(run_id,'running',snapshot())
            print('SEARCH_COMMITTED',r.json.dumps(q,ensure_ascii=False),flush=True)
            if 'transport_unavailable'in issues or 'run_time_budget_reached'in issues:break
            if any(e.startswith('search_')or e=='unverified_empty_inventory'for e in errors):
                issues.append('search_incomplete');break
            if max_details and len(done)>=max_details:break
            if len(queries)<len(specs):
                r.time.sleep(r.POLICY['search_delay_seconds']);browser.next_search()
        good=sum(v for k,v in quality.items()if k in GOOD)
        if not done or good/max(len(done),1)<r.POLICY['minimum_fulltext_fraction']:issues.append('fulltext_coverage')
        summary=snapshot();summary['pending_details_observed']=max(0,len(seen)-len(done))
        store.touch(run_id,'pilot'if limited else'warning'if issues else'complete',summary)
        print('RUN_RESULT',r.json.dumps(summary,ensure_ascii=False),flush=True)
        if issues and not limited:r.notify(summary)
        return 2 if issues else 0
    except BaseException as exc:
        issues.append('runtime_'+type(exc).__name__);summary=snapshot()
        store.touch(run_id,'failed',summary)
        if not limited:r.notify(summary)
        raise RuntimeError(type(exc).__name__)from None
    finally:
        try:
            if browser:browser.close()
        finally:conn.close()
