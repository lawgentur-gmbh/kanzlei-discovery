"""Private DB contract test using one existing genuine archive job. No network crawl.

Proves a committed checkpoint survives reconnect and a later failed transaction
cannot alter it. No synthetic job or description is stored, no deletes required.
"""
import os,uuid
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from runtime import Store,PersistenceError,digest,VERSION

def connect():
    return psycopg.connect(os.environ['STEPSTONE_DATABASE_URL'],autocommit=True,connect_timeout=20,sslmode='verify-full',sslrootcert=str(Path(__file__).parent/'supabase-ca.crt'))

def main():
    run_id=str(uuid.uuid4());key=digest('checkpoint_contract_probe');failed_key=digest('rollback_probe')
    with connect()as conn:
        conn.execute('insert into kr_portals.runs(id,status,parser_version,fingerprint,summary)values(%s,%s,%s,%s,%s)',(run_id,'pilot',VERSION,key,Jsonb({'db_contract_probe':True})))
        saved=conn.execute('select external_id,source_url,title,employer,location,published_raw from kr_portals.jobs order by first_seen limit 1').fetchone()
        if not saved:raise RuntimeError('existing_archive_required')
        native,url,title,employer,location,published=saved
        row=dict(Stepstone_ID=native,Titel_url=url,Job_Titel=title,Name_des_Unternehmens=employer,Standort=location,Erscheinen=published,Home_möglich='')
        Store(conn).checkpoint_page([row],run_id,key,'checkpoint_contract_probe',key,1)
    with connect()as conn:
        record=conn.execute('select row_data,search_keys from kr_portals.stepstone_candidates where external_id=%s',(native,)).fetchone()
        assert record and record[0]['Job_Titel']==title and key in record[1]
        try:Store(conn).checkpoint_page([{**row,'Job_Titel':'ROLLBACK_MUST_NOT_PERSIST'}],run_id,failed_key,None,key,1)
        except PersistenceError:pass
        else:raise RuntimeError('expected_not_null_failure_missing')
    with connect()as conn:
        record=conn.execute('select row_data,search_keys from kr_portals.stepstone_candidates where external_id=%s',(native,)).fetchone()
        assert record[0]['Job_Titel']==title and failed_key not in record[1]
        summary={'db_contract_probe':True,'reconnect_durability':True,'later_failure_rollback':True,'native_jobs_created':0,'native_texts_created':0}
        Store(conn).touch(run_id,'pilot',summary)
    print('CHECKPOINT_CONTRACT_PASS reconnect_durability=true later_failure_rollback=true synthetic_jobs=0',flush=True)

if __name__=='__main__':main()
