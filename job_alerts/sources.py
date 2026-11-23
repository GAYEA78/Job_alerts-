"""Collect once, then match all profiles. All adapters use the same HTTP client."""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlencode
import settings
from .http import SourceError, BudgetExceeded
from .locations import location_matches, search_states
from .matching import clean_text, title_excluded, canonical_url

SIMPLIFY_URL='https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json'
DISCOVERY={
    'greenhouse':r'(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)',
    'lever':r'jobs\.lever\.co/([A-Za-z0-9_-]+)',
    'ashby':r'jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)',
    'workday':r'https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)',
    'smartrecruiters':r'jobs\.smartrecruiters\.com/([A-Za-z0-9]+)/',
    'bamboohr':r'https?://([a-z0-9-]+)\.bamboohr\.com',
}


def timestamp(value):
    if isinstance(value,(int,float)):
        return value/1000 if value>1e11 else value
    if not value:
        return None
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError,TypeError):
        return None


def posted_on(value, now):
    t=str(value).lower()
    if 'today' in t or 'just posted' in t:
        return now
    if 'yesterday' in t:
        return now-86400
    m=re.search(r'(\d+)\+?\s*day',t)
    return now-int(m[1])*86400 if m else timestamp(value)


def normalize(company,title,locations,url,posted,description,source,**extra):
    return {'company':str(company or 'Unknown'),'title':str(title or ''),
            'locations':[str(l) for l in locations if l], 'url':canonical_url(url),
            'posted':timestamp(posted),'description':clean_text(description),
            'source':source,'description_complete':bool(description),**extra}


class Collector:
    def __init__(self, client, state, now=None):
        self.http=client
        self.state=state
        self.now=time.time() if now is None else now
        self.reports=[]
        self.lock=threading.Lock()

    def eligible(self,title,locs,posted=None,ambiguous=False):
        return bool(title and not title_excluded(title) and
                    (ambiguous or location_matches(locs)) and
                    (not posted or posted>=self.now-settings.HOURS_LOOKBACK*3600))

    def fetch(self,*args,**kwargs):
        return self.http.get(*args,**kwargs)

    def page_start(self,key):
        return int(self.state.data['cursors'].get(key,0))

    def cursor(self,key,value):
        with self.lock:
            self.state.data['cursors'][key]=value

    def run(self,name,fn):
        start=time.monotonic()
        jobs=[]
        status='ok'
        error=''
        try:
            for job in fn():
                if job.get('url'):
                    jobs.append(job)
        except BudgetExceeded as exc:
            status,error='deferred',str(exc)
        except SourceError as exc:
            status,error='failed',str(exc)
            if str(exc)=='HTTP 404':
                self.state.data.setdefault('dead_boards',{})[name]=self.now+7*86400
        except Exception as exc:
            # Never log credential-bearing request URLs or raw provider responses.
            status,error='failed','Invalid source data ('+type(exc).__name__+')'
        report={'source':name,'status':status,'jobs':len(jobs),'seconds':round(time.monotonic()-start,2)}
        if error:
            report['error']=error
        with self.lock:
            self.reports.append(report)
        return jobs

    def greenhouse(self,slug):
        data=self.fetch('https://boards-api.greenhouse.io/v1/boards/'+slug+'/jobs?content=true')
        for j in data.get('jobs',[]):
            title=j.get('title','')
            locs=[(j.get('location') or {}).get('name','')]
            # updated_at is NOT the original posting date. Keep unknown dates honest.
            posted=timestamp(j.get('first_published'))
            if self.eligible(title,locs,posted):
                yield normalize(slug,title,locs,j.get('absolute_url',''),posted,j.get('content',''),'Greenhouse',job_id=str(j.get('id','')))

    def lever(self,slug):
        data=self.fetch('https://api.lever.co/v0/postings/'+slug+'?mode=json')
        for j in data:
            cats=j.get('categories') or {}
            locs=[cats.get('location','')]+list(cats.get('allLocations') or [])
            posted=timestamp(j.get('createdAt'))
            if not self.eligible(j.get('text',''),locs,posted):
                continue
            desc='\n'.join([j.get('descriptionPlain') or j.get('description','')]+[s.get('content','') for s in j.get('lists') or []]+[j.get('additionalPlain','')])
            yield normalize(slug,j.get('text'),locs,j.get('hostedUrl',''),posted,desc,'Lever',job_id=str(j.get('id','')))

    def ashby(self,slug):
        data=self.fetch('https://api.ashbyhq.com/posting-api/job-board/'+slug+'?includeCompensation=true')
        for j in data.get('jobs',[]):
            locs=[j.get('location','')]+[s.get('location','') for s in j.get('secondaryLocations') or []]
            posted=timestamp(j.get('publishedAt'))
            if j.get('isListed') is False or not self.eligible(j.get('title',''),locs,posted):
                continue
            yield normalize(slug,j.get('title'),locs,j.get('jobUrl') or j.get('applyUrl',''),posted,j.get('descriptionHtml') or j.get('descriptionPlain',''),'Ashby',job_id=str(j.get('id','')))

    def workday(self,board):
        tenant,wd,site=board
        base='https://'+tenant+'.'+wd+'.myworkdayjobs.com'
        endpoint=base+'/wday/cxs/'+tenant+'/'+site
        key='workday:'+':'.join(board)
        offset=self.page_start(key)
        fetched=0
        for _ in range(settings.MAX_PAGES_PER_SOURCE):
            data=self.fetch(endpoint+'/jobs',payload={'limit':20,'offset':offset,'searchText':'','appliedFacets':{}})
            items=data.get('jobPostings') or []
            if not items:
                self.cursor(key,0)
                break
            for j in items:
                title=j.get('title','')
                path=j.get('externalPath','')
                locs=[j.get('locationsText','')]
                posted=posted_on(j.get('postedOn'),self.now)
                if not path or not self.eligible(title,locs,posted,ambiguous=bool(re.search(r'\d+ locations',locs[0],re.I))):
                    continue
                detail=self.fetch(endpoint+path,ttl=settings.DESCRIPTION_CACHE_SECONDS)
                info=detail.get('jobPostingInfo') or {}
                fetched+=1
                locs=[info.get('location') or locs[0]]+list(info.get('additionalLocations') or [])
                yield normalize(tenant,title,locs,base+'/'+site+path,timestamp(info.get('startDate')) or posted,info.get('jobDescription',''),'Workday',job_id=str(info.get('jobReqId','')))
            offset+=len(items)
            total=data.get('total')
            done=(isinstance(total,int) and offset>=total) or len(items)<20
            self.cursor(key,0 if done else offset)
            if done:
                break

    def smartrecruiters(self,company):
        endpoint='https://api.smartrecruiters.com/v1/companies/'+company+'/postings'
        key='smartrecruiters:'+company
        offset=self.page_start(key)
        fetched=0
        for _ in range(settings.MAX_PAGES_PER_SOURCE):
            data=self.fetch(endpoint+'?'+urlencode({'limit':100,'offset':offset}))
            items=data.get('content') or []
            for j in items:
                loc=j.get('location') or {}
                if str(loc.get('country','')).lower() not in ('','us','usa','united states'):
                    continue
                locs=[', '.join(filter(None,[loc.get('city'),loc.get('region')]))]
                posted=timestamp(j.get('releasedDate'))
                if not j.get('id') or not self.eligible(j.get('name',''),locs,posted):
                    continue
                detail=self.fetch(endpoint+'/'+str(j['id']),ttl=settings.DESCRIPTION_CACHE_SECONDS)
                sections=(detail.get('jobAd') or {}).get('sections') or {}
                desc='\n'.join(s.get('text','') for s in sections.values() if isinstance(s,dict))
                fetched+=1
                yield normalize(company,j.get('name'),locs,'https://jobs.smartrecruiters.com/'+company+'/'+str(j['id']),posted,desc,'SmartRecruiters',job_id=str(j['id']))
            offset+=len(items)
            total=data.get('totalFound')
            done=not items or len(items)<100 or (isinstance(total,int) and offset>=total)
            self.cursor(key,0 if done else offset)
            if done:
                break

    def bamboohr(self,company):
        endpoint='https://'+company+'.bamboohr.com/careers'
        data=self.fetch(endpoint+'/list')
        jobs=data.get('result') or []
        key='bamboohr:'+company
        start=self.page_start(key)%max(1,len(jobs))
        fetched=0
        for index in range(len(jobs)):
            pos=(start+index)%len(jobs)
            j=jobs[pos]
            loc=j.get('location') or {}
            locs=[', '.join(filter(None,[loc.get('city'),loc.get('state')]))]
            if self.eligible(j.get('jobOpeningName',''),locs) and j.get('id'):
                detail=self.fetch(endpoint+'/'+str(j['id'])+'/detail',ttl=settings.DESCRIPTION_CACHE_SECONDS)
                desc=(((detail.get('result') or {}).get('jobOpening') or {}).get('description',''))
                fetched+=1
                yield normalize(company,j.get('jobOpeningName'),locs,endpoint+'/'+str(j['id']),None,desc,'BambooHR',job_id=str(j['id']))
            self.cursor(key,(pos+1)%len(jobs))
            if fetched>=settings.DESCRIPTION_FETCH_LIMIT:
                break

    def muse(self):
        key='muse'
        page=self.page_start(key)
        locations=['New York, NY','Jersey City, NJ','Newark, NJ'] if settings.NY_NJ_ONLY else ['New York, NY','Boston, MA','Philadelphia, PA','Washington, DC','Atlanta, GA','Flexible / Remote']
        for _ in range(settings.MAX_PAGES_PER_SOURCE):
            data=self.fetch('https://www.themuse.com/api/public/jobs?'+urlencode([('level','Entry Level'),('page',page)]+[('location',l) for l in locations]))
            items=data.get('results') or []
            for j in items:
                yield normalize((j.get('company') or {}).get('name'),j.get('name'),[l.get('name','') for l in j.get('locations') or []],(j.get('refs') or {}).get('landing_page',''),j.get('publication_date'),j.get('contents',''),'The Muse',entry_verified=True)
            page+=1
            done=not items or page>=data.get('page_count',page+1)
            self.cursor(key,0 if done else page)
            if done:
                break

    def remotive(self):
        # Fetch the entire feed once, avoiding duplicate category requests.
        data=self.fetch('https://remotive.com/api/remote-jobs',ttl=settings.REMOTIVE_CACHE_SECONDS)
        for j in data.get('jobs',[]):
            where=j.get('candidate_required_location') or 'Worldwide'
            yield normalize(j.get('company_name'),j.get('title'),['Remote ('+where+')'],j.get('url',''),j.get('publication_date'),j.get('description',''),'Remotive',salary_text=j.get('salary',''))

    def remoteok(self):
        data=self.fetch('https://remoteok.com/api',ttl=settings.REMOTIVE_CACHE_SECONDS)
        for j in data:
            if not isinstance(j,dict) or not j.get('position'):
                continue
            where=j.get('location') or 'Worldwide'
            yield normalize(j.get('company'),j.get('position'),['Remote ('+str(where)+')'],j.get('url',''),j.get('date'),j.get('description',''),'RemoteOK')

    def usajobs(self):
        key='usajobs'
        page=max(1,self.page_start(key))
        for _ in range(settings.MAX_PAGES_PER_SOURCE):
            params={'JobCategoryCode':'2210;1550;0854;0501;0560;1101;1102;1160;0343','LocationName':';'.join(search_states()),'ResultsPerPage':100,'Page':page,'SortField':'opendate','SortDirection':'desc'}
            data=self.fetch('https://data.usajobs.gov/api/search?'+urlencode(params),headers={'User-Agent':os.environ['USAJOBS_EMAIL'],'Authorization-Key':os.environ['USAJOBS_API_KEY'],'Host':'data.usajobs.gov'})
            result=data.get('SearchResult') or {}
            items=result.get('SearchResultItems') or []
            for it in items:
                d=it.get('MatchedObjectDescriptor') or {}
                details=(d.get('UserArea') or {}).get('Details') or {}
                desc=str(details.get('JobSummary',''))+'\n'+str(d.get('QualificationSummary',''))
                yield normalize(d.get('OrganizationName'),d.get('PositionTitle'),[l.get('LocationName','') for l in d.get('PositionLocation') or []],d.get('PositionURI',''),d.get('PublicationStartDate'),desc,'USAJobs',description_complete=False)
            total=int(result.get('SearchResultCountAll',len(items)) or 0)
            done=not items or page*100>=total
            self.cursor(key,1 if done else page+1)
            page+=1
            if done:
                break

    def adzuna(self):
        # Broad category coverage, with rotating location/category requests.
        categories=('it-jobs','accounting-finance-jobs','consultancy-jobs','engineering-jobs','graduate-jobs')
        combos=[(loc,cat) for loc in search_states() for cat in categories]
        start=self.page_start('adzuna:search')%len(combos)
        for index in range(min(4,len(combos))):
            pos=(start+index)%len(combos)
            loc,category=combos[pos]
            key='adzuna:'+loc+':'+category
            page=max(1,self.page_start(key))
            # Rotate pages too; source TTL keeps within typical small API quotas.
            params={'app_id':os.environ['ADZUNA_APP_ID'],'app_key':os.environ['ADZUNA_APP_KEY'],'results_per_page':50,'max_days_old':7,'sort_by':'date','category':category,'where':loc}
            data=self.fetch('https://api.adzuna.com/v1/api/jobs/us/search/'+str(page)+'?'+urlencode(params),ttl=21600)
            items=data.get('results') or []
            for j in items:
                flag=j.get('salary_is_predicted')
                estimated=True if str(flag)=='1' else False if str(flag)=='0' else None
                salary={'min':j.get('salary_min'),'max':j.get('salary_max'),'currency':'USD','period':'year','is_estimate':estimated,'source':'Adzuna'}
                yield normalize((j.get('company') or {}).get('display_name'),j.get('title'),[(j.get('location') or {}).get('display_name','')],j.get('redirect_url',''),j.get('created'),j.get('description',''),'Adzuna',description_complete=False,salary=salary,job_id=str(j.get('id','')))
            self.cursor(key,1 if not items or page*50>=data.get('count',0) else page+1)
            self.cursor('adzuna:search',(pos+1)%len(combos))

    def simplify(self,data):
        for j in data:
            if not j.get('active') or not j.get('is_visible',True) or not j.get('url'):
                continue
            yield normalize(j.get('company_name'),j.get('title'),j.get('locations') or [],j['url'],j.get('date_posted'),'', 'Simplify',entry_verified=True,sponsorship=j.get('sponsorship',''),category=j.get('category',''))

    def collect(self):
        try:
            feed=self.fetch(SIMPLIFY_URL,ttl=21600)
            if not isinstance(feed,list):
                raise SourceError('Unexpected feed shape')
            jobs=self.run('Simplify',lambda:self.simplify(feed))
        except SourceError as exc:
            feed=[]
            jobs=[]
            self.reports.append({'source':'Simplify','status':'failed','jobs':0,'error':str(exc)})
        discovered={p:set() for p in DISCOVERY}
        for j in feed:
            for platform,pattern in DISCOVERY.items():
                m=re.search(pattern,j.get('url',''))
                if m:
                    discovered[platform].add(m.groups() if platform=='workday' else m[1])
        manual={'greenhouse':settings.GREENHOUSE_COMPANIES,'lever':settings.LEVER_COMPANIES,'ashby':settings.ASHBY_COMPANIES,'workday':settings.WORKDAY_COMPANIES,'smartrecruiters':settings.SMARTRECRUITERS_COMPANIES,'bamboohr':settings.BAMBOOHR_COMPANIES}
        tasks=[]
        # Interleave platforms so a large Workday list cannot starve other APIs.
        per_platform=[]
        for platform,found in discovered.items():
            boards=sorted(found|set(manual[platform]))
            per_platform.append([(platform,board) for board in boards])
        for i in range(max((len(x) for x in per_platform),default=0)):
            tasks.extend(x[i] for x in per_platform if i<len(x))
        dead=self.state.data.setdefault('dead_boards',{})
        tasks=[(platform,board) for platform,board in tasks if dead.get(platform+':'+str(board),0)<=self.now]
        start=self.page_start('boards')%max(1,len(tasks))
        selected=(tasks[start:]+tasks[:start])[:settings.MAX_BOARDS_PER_RUN]
        self.cursor('boards',(start+len(selected))%max(1,len(tasks)))
        work=[(platform+':'+str(board),lambda p=platform,b=board:getattr(self,p)(b)) for platform,board in selected]
        work.extend([('The Muse',self.muse),('Remotive',self.remotive),('RemoteOK',self.remoteok)])
        for name,required,fn in [('USAJobs',('USAJOBS_EMAIL','USAJOBS_API_KEY'),self.usajobs),('Adzuna',('ADZUNA_APP_ID','ADZUNA_APP_KEY'),self.adzuna)]:
            if all(os.environ.get(k) for k in required):
                work.append((name,fn))
            else:
                self.reports.append({'source':name,'status':'disabled','jobs':0,'reason':'API credentials not configured'})
        with ThreadPoolExecutor(max_workers=settings.MAX_PARALLEL) as pool:
            futures=[pool.submit(self.run,name,fn) for name,fn in work]
            for future in as_completed(futures):
                jobs.extend(future.result())
        self.state.data['discovery']={'known_boards':len(tasks),'scheduled_boards':len(selected),'updated_at':self.now}
        return jobs
