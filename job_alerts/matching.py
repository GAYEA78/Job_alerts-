"""Explainable early-career matching. Posting text is data, never instructions."""
import html
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import settings
from .locations import matching_locations
from .profiles import PROFILES

ENTRY = re.compile(r"\b(new[ -]?grad(?:uate)?s?|recent graduates?|entry[ -]level|early[ -]career|graduate (?:program|scheme|hire)|rotational program|campus (?:hire|recruitment|recruiting)|class of 20\d\d|no (?:prior |previous )?(?:professional |work )?experience (?:required|necessary))\b", re.I)
JUNIOR = re.compile(r"\b(junior|jr|trainee|apprentice)\b|\b(engineer|developer|analyst|consultant|specialist|associate|accountant)\s+(i|1)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|principal|lead|director|vice president|vp|chief|head of)\b|\b(engineer|developer|analyst)\s+(iii|iv|[3-9])\b", re.I)
EXCLUDE = re.compile(r"\b(intern|internship|co[ -]?op|recruiter|nurse|physician|attorney|paralegal|cashier|barista|warehouse|forklift|janitor|custodian|electrician|plumber|mechanic|security guard)\b", re.I)
WORDS = {'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
NUM = r"(?:\d{1,2}|"+'|'.join(WORDS)+r")"
YEARS = re.compile(r"\b("+NUM+r")\s*\+?\s*(?:[-–—]|to)?\s*(?:"+NUM+r")?\s*\+?\s*(?:years?|yrs?)\b", re.I)
OPTIONAL = re.compile(r"\b(preferred|preferably|desirable|nice to have|a plus|bonus|not required)\b", re.I)


def clean_text(value):
    text = html.unescape(str(value or ''))
    text = re.sub(r"</(?:p|li|div|h[1-6])\s*>|<br\s*/?>", '\n', text, flags=re.I)
    text = re.sub(r"<[^>]*>", ' ', text)
    return '\n'.join(re.sub(r'\s+', ' ', s).strip() for s in text.splitlines() if s.strip())


def has_word(text, phrase):
    return bool(re.search(r'(?<!\w)'+re.escape(phrase)+r'(?!\w)', text, re.I))


def title_excluded(title):
    if EXCLUDE.search(title) or SENIOR.search(title):
        return True
    # "Member of Technical Staff" is a title family, not a staff seniority level.
    if re.search(r'\bstaff\b', title, re.I) and not re.search(r'member of (?:the )?technical staff', title, re.I):
        return True
    if re.search(r'\bmanager\b', title, re.I) and not re.search(r'(associate|junior|graduate|entry.level|technical account) .*manager|associate product manager', title, re.I):
        return True
    return False


def required_years(text):
    """Use mandatory experience clauses, not benefits, degree duration or preferences."""
    requirements = []
    section_optional = False
    for line in clean_text(text).splitlines():
        if re.match(r'^(preferred|nice.to.have|bonus) (qualifications|skills|experience)', line, re.I):
            section_optional = True
        elif re.match(r'^(required|minimum|basic) (qualifications|skills|experience)|^requirements', line, re.I):
            section_optional = False
        if section_optional:
            continue
        for sentence in re.split(r'(?<=[.!?;])\s+', line):
            alternatives = []
            for clause in re.split(r'\s+or\s+', sentence):
                if OPTIONAL.search(clause):
                    continue
                counts=[]
                for match in YEARS.finditer(clause):
                    tail=clause[match.end():match.end()+65]
                    before=clause[max(0,match.start()-45):match.start()]
                    if re.match(r'\s*(?:of )?(degree|college|university|school|undergraduate|program)\b', tail, re.I):
                        continue
                    if not re.search(r'experience|\bexp\b|working|professional|background|\bin\b', tail+' '+before, re.I):
                        continue
                    raw=match.group(1).lower()
                    counts.append(WORDS[raw] if raw in WORDS else int(raw))
                if counts:
                    alternatives.append(max(counts))
            if alternatives:
                requirements.append(min(alternatives))
    return max(requirements) if requirements else None


def advanced_degree_required(text):
    for clause in re.split(r'[\n.;]', clean_text(text)):
        if re.search(r'\b(ph\.?d|doctorate|doctoral|masters?|master.s|mba)\b', clause, re.I):
            if not OPTIONAL.search(clause) and re.search(r'requir|must|minimum', clause, re.I) and not re.search(r'bachelor|undergraduate|\bb\.?s\.?\b', clause, re.I):
                return True
    return False


def canonical_url(url):
    try:
        p=urlsplit(str(url))
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
            return ''
        host=p.hostname.lower().replace('job-boards.greenhouse.io','boards.greenhouse.io')
        # Retain job identifiers and meaningful query parameters.
        query=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('source','ref','referrer','gh_src','lever-source','lever-origin','trackingid')]
        return urlunsplit(('https',host,p.path.rstrip('/'),urlencode(sorted(query)),''))
    except ValueError:
        return ''


def job_key(job):
    return canonical_url(job.get('url',''))


def merge_jobs(jobs):
    merged={}
    for original in jobs:
        key=job_key(original)
        if not key:
            continue
        job=dict(original)
        job['url']=key
        previous=merged.get(key)
        if previous:
            richer = job if len(job.get('description','')) > len(previous.get('description','')) else previous
            other = previous if richer is job else job
            result={**other,**richer}
            result['locations']=list(dict.fromkeys(previous.get('locations',[])+job.get('locations',[])))
            result['entry_verified']=previous.get('entry_verified',False) or job.get('entry_verified',False)
            result['description_complete']=previous.get('description_complete',False) or job.get('description_complete',False)
            result['posted']=previous.get('posted') or job.get('posted')
            result['sources']=sorted(set(previous.get('sources',[previous.get('source','')])+job.get('sources',[job.get('source','')])))
            merged[key]=result
        else:
            job['sources']=[job.get('source','')]
            merged[key]=job
    return list(merged.values())


def evaluate(job, profile, now):
    title=job.get('title','')
    desc=job.get('description','')
    locs=matching_locations(job.get('locations',[]))
    if not title or title_excluded(title) or not locs:
        return None
    if job.get('posted') and job['posted'] < now-settings.HOURS_LOOKBACK*3600:
        return None
    if advanced_degree_required(desc):
        return None
    years=required_years(desc)
    if years is not None and years > settings.MAX_REQUIRED_YEARS:
        return None  # Entry-level wording never cancels an explicit requirement.
    p=PROFILES[profile]
    title_fit=any(has_word(title,t) for t in p['titles'])
    title_fit=title_fit or (any(has_word(title,n) for n in p['nouns']) and any(has_word(title,q) for q in p['qualifiers']))
    if profile=='tech' and re.search(r'\b(sql|sql server|database|business intelligence) (developer|administrator|analyst)\b',title,re.I):
        title_fit=True
    skills=[s for s in p['skills'] if has_word(desc,s)]
    if not title_fit and len(skills)<3:
        return None
    # Business/finance scoring must not admit software roles merely mentioning SQL.
    if profile=='finance' and re.search(r'\b(software|mlops|machine learning|devops|backend|frontend) (engineer|developer)\b',title,re.I):
        return None
    if profile=='tech' and not title_fit and not re.search(p['degree_pattern'],desc,re.I):
        return None
    year=settings.GRADUATION_YEAR.get(profile)
    classes=re.findall(r'\bclass of (20\d\d)\b',title+' '+desc,re.I)
    if year and classes and str(year) not in classes:
        return None
    explicit=bool(ENTRY.search(title) or JUNIOR.search(title) or years==0 or job.get('entry_verified'))
    desc_entry=bool(ENTRY.search(desc))
    known_entry=explicit or desc_entry
    if not known_entry and not settings.INCLUDE_UNKNOWN_EXPERIENCE:
        return None
    # Unknown level needs a real description, not an empty collector result.
    if not known_entry and (len(desc)<100 or not job.get('description_complete',False)):
        return None
    if settings.REQUIRE_SPONSORSHIP.get(profile) and job.get('sponsorship') not in ('Offers Sponsorship','yes'):
        return None
    floor=settings.MIN_ANNUAL_SALARY.get(profile)
    salary=job.get('salary') or {}
    if floor and salary.get('currency')=='USD' and salary.get('period')=='year' and salary.get('max',0) and salary['max']<floor:
        return None
    preferred=[s for s in settings.PREFERRED_SKILLS.get(profile,()) if has_word(desc,s)]
    reasons=['NY/NJ location' if settings.NY_NJ_ONLY else 'Preferred location']
    reasons.append('Entry-level evidence' if known_entry else 'Experience not specified — verify requirements')
    if preferred:
        reasons.append('Skills: '+', '.join(preferred[:5]))
    if years is not None:
        reasons.append(str(years)+' years required')
    if not job.get('description_complete',False):
        reasons.append('Full requirements unavailable — verify before applying')
    score=min(100,40+20*bool(title_fit)+20*known_entry+min(15,len(preferred)*3)+5*bool(job.get('description_complete')))
    result={**job,'locations':locs,'score':score,'reasons':reasons,'required_years':years,'confidence':'strong' if known_entry and job.get('description_complete') else 'review'}
    return result
