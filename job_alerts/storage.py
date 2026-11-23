"""Atomic state, legacy migration, queued alerts and application tracking."""
import hashlib
import json
import os
import time
from pathlib import Path
from .matching import canonical_url, job_key


def atomic_json(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp,path)


def read_json(path, default):
    path=Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError) as exc:
        raise RuntimeError('Cannot read state '+str(path)+'; refusing to forget delivery history') from exc


def recipient_key(address):
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()[:24]


class State:
    def __init__(self, home):
        self.home=Path(home)
        self.path=self.home/'alerts_v2.json'
        self.data=read_json(self.path,{'version':2,'profiles':{},'cursors':{},'cooldowns':{}})
        if not isinstance(self.data,dict) or self.data.get('version')!=2:
            raise RuntimeError('Unsupported alert state format')
        for profile,filename in [('tech','seen_jobs.json'),('finance','seen_jobs_finance.json')]:
            p=self.data['profiles'].setdefault(profile,{'sent':{},'pending':{},'applications':{},'last_digest':0})
            if 'legacy_urls' not in p:
                old=read_json(self.home/filename,[])
                if not isinstance(old,list):
                    raise RuntimeError('Invalid legacy history: '+filename)
                # Old company/title fingerprints are deliberately discarded.
                p['legacy_urls']=sorted({canonical_url(s) for s in old if isinstance(s,str) and s.startswith(('http://','https://'))}-{''})
            # Migrate old Adzuna redirect keys without forgetting accepted mail
            # or application statuses. Distinct ad IDs remain separate jobs.
            sent={}
            for url,deliveries in p['sent'].items():
                target=sent.setdefault(job_key({'url':url}),{})
                for rid,when in deliveries.items():
                    target[rid]=max(target.get(rid,0),when)
            p['sent']=sent
            applications={}
            for url,entry in p['applications'].items():
                key=job_key({'url':url})
                if entry.get('updated_at',0)>=applications.get(key,{}).get('updated_at',0):
                    applications[key]=entry
            p['applications']=applications
            pending={}
            for job in p['pending'].values():
                key=job_key(job)
                if job.get('last_seen',0)>=pending.get(key,{}).get('last_seen',0):
                    pending[key]=job
            p['pending']=pending
            p['legacy_urls']=sorted({job_key({'url':url}) for url in p['legacy_urls']}-{''})

    def save(self):
        atomic_json(self.path,self.data)

    def profile(self, name):
        return self.data['profiles'][name]

    def enqueue(self, profile, jobs, now):
        p=self.profile(profile)
        legacy=set(p['legacy_urls'])
        for job in jobs:
            key=job_key(job)
            if key in legacy or p['applications'].get(key,{}).get('status') in ('applied','interviewing','rejected','dismissed'):
                continue
            # Keep enough to retry/deliver; don't publish full job descriptions in Git.
            compact={k:v for k,v in job.items() if k not in ('description',)}
            compact['first_seen']=p['pending'].get(key,{}).get('first_seen',now)
            compact['last_seen']=now
            p['pending'][key]=compact

    def pending(self, profile, recipient):
        p=self.profile(profile)
        rid=recipient_key(recipient)
        return [j for key,j in p['pending'].items() if rid not in p['sent'].get(key,{}) and p['applications'].get(key,{}).get('status') not in ('applied','interviewing','rejected','dismissed')]

    def delivered(self, profile, recipient, jobs, now):
        p=self.profile(profile)
        rid=recipient_key(recipient)
        for job in jobs:
            p['sent'].setdefault(job_key(job),{})[rid]=now
        self.save()  # Checkpoint every successful recipient separately.

    def prune(self, profile, recipients, now, retention_days):
        p=self.profile(profile)
        ids={recipient_key(r) for r in recipients}
        for key in list(p['pending']):
            if ids and ids.issubset(p['sent'].get(key,{})):
                del p['pending'][key]
        cutoff=now-retention_days*86400
        # Deterministic timestamp retention; never truncate an unordered set.
        for key, deliveries in list(p['sent'].items()):
            if deliveries and max(deliveries.values())<cutoff and key not in p['pending'] and key not in p['applications']:
                del p['sent'][key]

    def track(self, profile, url, status):
        if status not in ('saved','applied','interviewing','rejected','dismissed','reset'):
            raise ValueError('Unsupported application status')
        key=job_key({'url':url})
        if not key:
            raise ValueError('A valid HTTP(S) job URL is required')
        p=self.profile(profile)
        if status=='reset':
            p['applications'].pop(key,None)
        else:
            p['applications'][key]={'status':status,'updated_at':time.time()}
        self.save()
