"""CLI orchestration. Preview is the default; --send explicitly enables delivery."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
import settings
from .storage import State, atomic_json
from .http import HttpClient
from .sources import Collector
from .matching import match_profiles,merge_jobs
from .locations import matching_locations
from .delivery import configuration,build_email,send_email
from .semantic import classify


def ranked(jobs):
    return sorted(jobs,key=lambda j:(-j['score'],-(j.get('posted') or 0),j['url']))


def deliver_profile(state,profile,now,degraded=False,send=send_email):
    sender,recipients,password=configuration(profile)
    p=state.profile(profile)
    due=now-p['last_digest']>=settings.DIGEST_MIN_HOURS*3600
    delivered=0
    for recipient in recipients:
        jobs=[]
        for original in state.pending(profile,recipient):
            job=dict(original)
            # Reapply the switch to jobs queued before a configuration change.
            locs=matching_locations(job.get('locations',[]))
            if not locs:
                continue
            job['locations']=locs
            if due or (settings.IMMEDIATE_HIGH_MATCH_ALERTS and job['score']>=settings.HIGH_MATCH_SCORE):
                jobs.append(job)
        jobs=ranked(jobs)
        if not jobs and not (due and settings.EMAIL_WHEN_EMPTY):
            continue
        batch=jobs[:settings.MAX_EMAIL_JOBS]
        subject,body,text=build_email(profile,batch,max(0,len(jobs)-len(batch)),degraded)
        send(sender,recipient,password,subject,body,text)
        state.delivered(profile,recipient,batch,now)
        delivered+=len(batch)
    if due:
        p['last_digest']=now
    state.prune(profile,recipients,now,settings.STATE_RETENTION_DAYS)
    state.save()
    return delivered


def main(default_profile=None):
    parser=argparse.ArgumentParser(description='Collect, rank and preview job alerts')
    parser.add_argument('--profile',choices=('tech','finance','all'),default=default_profile or 'all')
    parser.add_argument('--send',action='store_true',help='Send email; otherwise only write previews')
    parser.add_argument('--dry-run',action='store_true',help='Explicitly disable email delivery and AI calls')
    parser.add_argument('--input',type=Path,help='Read normalized JSON fixtures instead of network sources')
    parser.add_argument('--output',type=Path,default=Path('output'))
    args=parser.parse_args()
    home=Path(os.environ.get('JOB_ALERT_HOME','state'))
    home.mkdir(parents=True,exist_ok=True)
    now=time.time()
    state=State(home)
    send=args.send and not args.dry_run
    if args.input:
        raw=json.loads(args.input.read_text(encoding='utf-8'))
        reports=[{'source':'offline input','status':'ok','jobs':len(raw)}]
        metrics={}
    else:
        client=HttpClient(home/'cache',state.data['cooldowns'])
        collector=Collector(client,state,now)
        raw=collector.collect()
        reports=collector.reports
        metrics={'requests':client.requests,'cache_hits':client.hits,'retries':client.retries}
        client.prune()
    successful=sum(r['status']=='ok' for r in reports)
    failed=sum(r['status']=='failed' for r in reports)
    deferred=sum(r['status']=='deferred' for r in reports)
    ratio=failed/max(1,successful+failed)
    degraded=ratio>=settings.SOURCE_FAILURE_WARNING_RATIO or deferred>0
    unhealthy=successful==0 or ratio>=settings.SOURCE_FAILURE_ERROR_RATIO
    summary={'raw_jobs':len(raw),'sources_ok':successful,'sources_failed':failed,'sources_deferred':deferred,'ny_nj_only':settings.NY_NJ_ONLY,'degraded':degraded,'profiles':{},**metrics}
    merged=merge_jobs(raw)
    matches=match_profiles(merged,now)
    summary['shared_matches']=sum(len(j.get('matching_profiles',[]))>1 for j in matches['tech'])
    profiles=['tech','finance'] if args.profile=='all' else [args.profile]
    errors=[]
    args.output.mkdir(parents=True,exist_ok=True)
    for profile in profiles:
        jobs=ranked(matches[profile])
        if send and settings.ENABLE_AI:
            uncertain=[j for j in jobs if j['confidence']=='review']
            verdicts=classify(uncertain,profile,state,now)
            rejected={uncertain[i]['url'] for i,value in verdicts.items() if value is False}
            jobs=[j for j in jobs if j['url'] not in rejected]
        summary['profiles'][profile]={'matches':len(jobs),'delivered':0}
        atomic_json(args.output/(profile+'_matches.json'),[{k:v for k,v in j.items() if k!='description'} for j in jobs])
        subject,body,text=build_email(profile,jobs[:settings.MAX_EMAIL_JOBS],max(0,len(jobs)-settings.MAX_EMAIL_JOBS),degraded)
        (args.output/(profile+'_preview.html')).write_text(body,encoding='utf-8')
        if send:
            state.enqueue(profile,jobs,now)
            state.save()
            try:
                summary['profiles'][profile]['delivered']=deliver_profile(state,profile,now,degraded)
            except Exception as exc:
                errors.append(profile+': delivery or state save failed ('+type(exc).__name__+')')
    if send:
        state.save()
    # Dry-run collection may warm disposable HTTP cache, but does not save
    # authoritative state, consume pending alerts, or mark anything delivered.
    summary['errors']=errors
    atomic_json(args.output/'health.json',{'summary':summary,'sources':reports})
    print(json.dumps(summary,indent=2))
    step_summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary,'a',encoding='utf-8') as f:
            f.write('## Job alert health\n\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
            f.write('\n| Source | Status | Jobs | Detail |\n|---|---|---:|---|\n')
            for r in sorted(reports,key=lambda r:(r['status'],r['source'])):
                f.write('| '+r['source'].replace('|','/')+' | '+r['status']+' | '+str(r.get('jobs',0))+' | '+r.get('error',r.get('reason',''))+' |\n')
    if degraded:
        print('::warning::Job coverage is partial; inspect the source health summary.')
    return 1 if errors or unhealthy else 0


if __name__=='__main__':
    sys.exit(main())
