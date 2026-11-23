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
CROSSOVER_ROLE = re.compile(r'\b(analyst|consultant|associate|specialist|coordinator|researcher)\b', re.I)
BUILD_ROLE = re.compile(r'\b(engineer|engineering|developer|programmer|scientist|technical staff)\b', re.I)
SPECIALIST_FINANCE = re.compile(r'\b(accountant|accounting|auditor|audit|assurance|tax)\b', re.I)
DEGREE_FIELDS = {
    'tech': r'computer science|computer engineering|software engineering|information (?:systems|technology)|\bstem\b|mathematics|\bmath\b|statistics|data science|quantitative|engineering',
    'finance': r'\bfinance\b|financial|accounting|economics|business|management|information systems|\bstem\b|mathematics|\bmath\b|statistics|quantitative',
}
NAMED_DEGREE = re.compile(r'computer science|engineering|information systems|information technology|data science|\bstem\b|mathematics|\bmath\b|statistics|finance|accounting|economics|business|management|biology|chemistry|physics|nursing|law|psychology', re.I)


def qualification_lines(text):
    """Ignore preferred sections when checking mandatory academic restrictions."""
    optional=False
    for line in clean_text(text).splitlines():
        if re.match(r'^(preferred|nice.to.have|bonus) (qualifications|skills|experience)|^ideally',line,re.I):
            optional=True
        elif re.match(r'^(required|minimum|basic) (qualifications|skills|experience)|^requirements|^to qualify',line,re.I):
            optional=False
        if not optional:
            for clause in re.split(r'(?<=[.;])\s+',line):
                if not OPTIONAL.search(clause):
                    yield clause


def degree_compatible(text, profile):
    """True = explicitly accepted, False = conflicting field, None = unspecified."""
    result=None
    for clause in qualification_lines(text):
        if not re.search(r'\bdegree\b|bachelor|undergraduate',clause,re.I):
            continue
        if re.search(r'any (?:academic )?(?:field|discipline|major)|all (?:fields|disciplines|majors)|no (?:college )?degree',clause,re.I):
            result=True
        elif re.search(DEGREE_FIELDS[profile],clause,re.I):
            result=True
        elif NAMED_DEGREE.search(clause) and not re.search(r'or equivalent (?:work |professional )?experience',clause,re.I):
            return False
    return result


def accounting_credential_required(text):
    for clause in qualification_lines(text):
        if re.search(r'\bcpa\b|certified public accountant',clause,re.I) and re.search(r'requir|must|eligib|course\s?work|credit hours|sufficient course',clause,re.I):
            return True
    return False


def role_evidence(job, profile):
    """Match by role and qualifications, independently of the collection source."""
    title=job.get('title','')
    desc=job.get('description','')
    p=PROFILES[profile]
    has_role=any(has_word(title,n) for n in p['nouns'])
    direct=any(has_word(title,t) for t in p['titles']) or (has_role and any(has_word(title,q) for q in p['qualifiers']))
    skills=[s for s in p['skills'] if has_word(desc,s)]
    degree=degree_compatible(desc,profile)
    if degree is False:
        return None
    if profile=='tech':
        if accounting_credential_required(desc):
            return None
        if re.search(r'\b(sql|sql server|database|business intelligence) (developer|administrator|analyst)\b',title,re.I):
            direct=True
        if job.get('entry_verified') and has_role and job.get('category') in ('Software','Software Engineering','AI/ML/Data','Data Science, AI & Machine Learning'):
            direct=True
        # Physical infrastructure and financial assurance are not CS evidence.
        specialist=bool(SPECIALIST_FINANCE.search(title))
        if specialist and degree is not True and len(skills)<2:
            direct=False
        if direct:
            return 'Role fits Tech & CS',False
        if has_role and len(skills)>=3 and degree is True:
            return 'Technical skills and accepted degree',True
        other=PROFILES['finance']
        business_role=any(has_word(title,t) for t in other['titles']) or (bool(CROSSOVER_ROLE.search(title)) and any(has_word(title,q) for q in other['qualifiers']))
        if settings.INCLUDE_CROSSOVER_ROLES and business_role and CROSSOVER_ROLE.search(title) and degree is not False:
            if degree is True:
                return 'Crossover: business/finance role accepts a CS, STEM or unrestricted degree',True
            if re.search(r'\b(analyst|consultant|researcher)\b',title,re.I) or len(skills)>=2:
                return 'Possible crossover: business/finance role; verify degree eligibility',True
    else:
        # A technical title needs affirmative finance/business degree evidence;
        # employment at a bank or a mention of Excel alone is not enough.
        if BUILD_ROLE.search(title):
            analytical=bool(re.search(r'\b(data|analytics|quantitative|quant|financial|risk|trading)\b',title,re.I))
            if settings.INCLUDE_CROSSOVER_ROLES and analytical and degree is True and len(skills)>=2:
                return 'Crossover: analytical role accepts a finance/business or quantitative degree',True
            return None
        if direct:
            return 'Role fits Finance & Business',False
        if settings.INCLUDE_CROSSOVER_ROLES and CROSSOVER_ROLE.search(title) and degree is True and len(skills)>=2:
            return 'Crossover: business skills and accepted degree',True
    return None


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
        clauses = re.split(r'(?<=[.!?;])\s+|,\s*(?=(?:'+NUM+r'|preferred|required|minimum)\b)|\s+(?:and|but)\s+(?='+NUM+r'\b)', line, flags=re.I)
        for sentence in clauses:
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
    url=canonical_url(job.get('url',''))
    p=urlsplit(url)
    # Adzuna se/v identify a search or redirect, not a different opening.
    # Preserve them in the application URL; use the stable ad ID for history.
    if p.hostname in ('www.adzuna.com','adzuna.com'):
        match=re.fullmatch(r'/(?:jobs/)?(?:land/ad|details)/(\d+)',p.path)
        if match:
            return 'https://www.adzuna.com/land/ad/'+match[1]
    return url


def merge_jobs(jobs):
    merged={}
    for original in jobs:
        key=job_key(original)
        if not key:
            continue
        job=dict(original)
        job['url']=canonical_url(job.get('url',''))
        previous=merged.get(key)
        if previous:
            quality=lambda j:(bool(j.get('description_complete') and j.get('description')),len(j.get('description','')))
            richer = job if quality(job)>quality(previous) else previous
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
    evidence=role_evidence(job,profile)
    if evidence is None:
        return None
    role_reason,crossover=evidence
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
    adzuna=salary.get('source')=='Adzuna' or job.get('source')=='Adzuna'
    confirmed=salary.get('is_estimate') is not True and (not adzuna or salary.get('is_estimate') is False)
    if floor and confirmed and salary.get('currency')=='USD' and salary.get('period')=='year' and salary.get('max',0) and salary['max']<floor:
        return None
    preferred=[s for s in settings.PREFERRED_SKILLS.get(profile,()) if has_word(desc,s)]
    reasons=['NY/NJ location' if settings.NY_NJ_ONLY else 'Preferred location']
    reasons.append(role_reason)
    reasons.append('Entry-level evidence' if known_entry else 'Experience not specified — verify requirements')
    if preferred:
        reasons.append('Skills: '+', '.join(preferred[:5]))
    if years is not None:
        reasons.append(str(years)+' years required')
    if not job.get('description_complete',False):
        reasons.append('Full requirements unavailable — verify before applying')
    complete=bool(job.get('description_complete'))
    uncertain_degree=role_reason.startswith('Possible crossover')
    score=min(100,40+20+20*known_entry+min(15,len(preferred)*3)+5*complete)
    if not complete:
        score=min(score,60)
    elif uncertain_degree:
        score=min(score,70)
    result={**job,'locations':locs,'score':score,'reasons':reasons,'required_years':years,'crossover':crossover,'confidence':'strong' if known_entry and complete and not uncertain_degree else 'review'}
    return result


def match_profiles(jobs, now):
    """A posting may qualify for both profiles; neither profile consumes it."""
    matched={profile:[] for profile in PROFILES}
    for job in jobs:
        results={profile:result for profile in PROFILES if (result:=evaluate(job,profile,now)) is not None}
        for profile,result in results.items():
            result['matching_profiles']=list(results)
            if len(results)>1:
                other=next(name for name in results if name!=profile)
                result['reasons'].append('Also matches '+PROFILES[other]['label'])
            matched[profile].append(result)
    return matched
