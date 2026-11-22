"""Shared disk cache, per-host pacing, bounded retries and persisted cooldowns."""
import gzip
import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
import settings


class SourceError(RuntimeError):
    pass


class BudgetExceeded(SourceError):
    pass


def retry_after(value, now):
    try:
        return max(0,float(value))
    except (ValueError,TypeError):
        try:
            return max(0,parsedate_to_datetime(value).timestamp()-now)
        except (ValueError,TypeError,OverflowError):
            return 0


class HttpClient:
    def __init__(self, cache_dir, cooldowns, budget=None, opener=None, clock=None, sleeper=None):
        self.cache=Path(cache_dir)
        self.cache.mkdir(parents=True,exist_ok=True)
        self.cooldowns=cooldowns
        self.budget=settings.MAX_REQUESTS_PER_RUN if budget is None else budget
        self.opener=opener or urllib.request.urlopen
        self.clock=clock or time.time
        self.sleep=sleeper or time.sleep
        self.lock=threading.Lock()
        self.host_locks={}
        self.next_request={}
        self.requests=0
        self.hits=0
        self.retries=0
        self.group_requests={}

    def get(self,url,payload=None,ttl=None,headers=None):
        ttl=settings.SOURCE_CACHE_SECONDS if ttl is None else ttl
        # Credentials influence the cache key but are never stored in filenames/logs.
        signature=json.dumps([url,payload,headers or {}],sort_keys=True)
        key=hashlib.sha256(signature.encode()).hexdigest()
        path=self.cache/(key+'.json.gz')
        if path.exists() and self.clock()-path.stat().st_mtime<ttl:
            try:
                with gzip.open(path,'rt',encoding='utf-8') as f:
                    data=json.load(f)
                with self.lock:
                    self.hits+=1
                return data
            except (OSError,ValueError,EOFError):
                pass
        host=urlsplit(url).hostname or ''
        group='workday' if host.endswith('.myworkdayjobs.com') else host
        with self.lock:
            host_lock=self.host_locks.setdefault(group,threading.Lock())
        with host_lock:
            if self.cooldowns.get(group,0)>self.clock():
                raise SourceError('Source cooling down after an earlier failure')
            interval=settings.WORKDAY_INTERVAL_SECONDS if group=='workday' else settings.HOST_INTERVAL_SECONDS
            for attempt in range(settings.HTTP_ATTEMPTS):
                with self.lock:
                    if self.requests>=self.budget:
                        raise BudgetExceeded('Request budget reached; remaining work deferred')
                    if group=='workday' and self.group_requests.get(group,0)>=settings.MAX_WORKDAY_REQUESTS_PER_RUN:
                        raise BudgetExceeded('Workday request budget reached; remaining work deferred')
                    self.requests+=1
                    self.group_requests[group]=self.group_requests.get(group,0)+1
                wait=self.next_request.get(group,0)-self.clock()
                if wait>0:
                    self.sleep(wait)
                self.next_request[group]=self.clock()+interval
                body=json.dumps(payload).encode() if payload is not None else None
                req=urllib.request.Request(url,data=body,headers={
                    'User-Agent':'personal-job-alerts/2.0', 'Accept':'application/json',
                    **({'Content-Type':'application/json'} if body is not None else {}),
                    **(headers or {}),
                })
                try:
                    with self.opener(req,timeout=30) as response:
                        data=json.loads(response.read().decode('utf-8'))
                    temp=path.with_suffix('.tmp')
                    with gzip.open(temp,'wt',encoding='utf-8') as f:
                        json.dump(data,f)
                    temp.replace(path)
                    return data
                except urllib.error.HTTPError as exc:
                    if exc.code not in (429,500,502,503,504):
                        raise SourceError('HTTP '+str(exc.code)) from None
                    delay=max(retry_after(exc.headers.get('Retry-After'),self.clock()),2**attempt+random.random())
                    if delay>settings.MAX_INLINE_RETRY_SECONDS or attempt+1==settings.HTTP_ATTEMPTS:
                        self.cooldowns[group]=self.clock()+max(delay,900 if exc.code==429 else 120)
                        raise SourceError('HTTP '+str(exc.code)+'; retry deferred') from None
                except (urllib.error.URLError,TimeoutError,ConnectionError,OSError,ValueError):
                    delay=2**attempt
                    if attempt+1==settings.HTTP_ATTEMPTS:
                        raise SourceError('Network timeout or invalid source response') from None
                self.retries+=1
                self.sleep(delay)
        raise SourceError('Source unavailable')

    def prune(self):
        # Cache is disposable; authoritative delivery state is kept separately.
        files=sorted(self.cache.glob('*.json.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
        total=0
        for path in files:
            total+=path.stat().st_size
            if total>100*1024*1024 or self.clock()-path.stat().st_mtime>7*86400:
                path.unlink(missing_ok=True)
