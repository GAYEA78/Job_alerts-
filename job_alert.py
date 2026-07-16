#!/usr/bin/env python3

import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlencode

# SETTINGS 

GMAIL_ADDRESS = "gayearona89@gmail.com"
GMAIL_APP_PASSWORD = "PASTE-APP-PASSWORD-HERE"

# Everyone who should receive the job emails:
EMAIL_RECIPIENTS = ["gayearona89@gmail.com", "Kayla.imbriale@gmail.com"]

HOURS_LOOKBACK = 48          # how recent counts as "newly posted"
EMAIL_WHEN_EMPTY = False     # email even when nothing new?
MAX_EMAIL_JOBS = 120         # cap per email; extras arrive next run

# Where you want to work. The East Coast states below drive everything:
# the location filter plus the USAJobs / Adzuna / The Muse searches.
# Delete any state you don't want (PA, VT and WV are included even
# though they're inland, since most people count them as East Coast).
EAST_COAST_STATES = {
    "ME": "Maine", "NH": "New Hampshire", "VT": "Vermont",
    "MA": "Massachusetts", "RI": "Rhode Island", "CT": "Connecticut",
    "NY": "New York", "NJ": "New Jersey", "PA": "Pennsylvania",
    "DE": "Delaware", "MD": "Maryland", "DC": "District of Columbia",
    "VA": "Virginia", "WV": "West Virginia", "NC": "North Carolina",
    "SC": "South Carolina", "GA": "Georgia", "FL": "Florida",
}

# Metro names that job boards often list without a state attached.
EAST_COAST_CITIES = [
    "nyc", "new york city", "brooklyn", "manhattan", "queens",
    "long island", "albany", "buffalo", "rochester",
    "jersey city", "hoboken", "newark", "princeton",
    "boston", "cambridge", "philadelphia", "philly", "pittsburgh",
    "washington dc", "baltimore", "arlington", "alexandria",
    "reston", "mclean", "tysons", "richmond", "virginia beach",
    "norfolk", "raleigh", "durham", "charlotte", "charleston",
    "atlanta", "savannah", "miami", "fort lauderdale", "orlando",
    "tampa", "jacksonville", "providence", "hartford", "stamford",
    "new haven", "wilmington", "portsmouth", "burlington",
]

LOCATION_KEYWORDS = ([s.lower() for s in EAST_COAST_STATES.values()]
                     + EAST_COAST_CITIES)
INCLUDE_REMOTE = True        # include remote / hybrid / WFH US jobs

# The Muse only returns jobs for locations you explicitly ask for,
# so list the metros you care about (unknown names are ignored).
MUSE_LOCATIONS = [
    "New York, NY", "Jersey City, NJ", "Boston, MA",
    "Philadelphia, PA", "Pittsburgh, PA", "Washington, DC",
    "Baltimore, MD", "Arlington, VA", "Richmond, VA",
    "Charlotte, NC", "Raleigh, NC", "Durham, NC", "Atlanta, GA",
    "Miami, FL", "Orlando, FL", "Tampa, FL", "Jacksonville, FL",
    "Hartford, CT", "Stamford, CT", "Providence, RI",
    "Portland, ME", "Manchester, NH", "Burlington, VT",
    "Charleston, SC", "Wilmington, DE", "Flexible / Remote",
]

# Each Adzuna search below costs one API call per run. On the free
# tier with the 30-minute schedule that adds up, so trim this list
# (or slow the cron) if Adzuna starts returning rate-limit errors.
ADZUNA_SEARCHES = list(EAST_COAST_STATES.values())


# Auto-discovery reads the Simplify feed and extracts every company
# career board it references (several hundred), then watches those
# boards directly. Set False to only use the manual lists below.
AUTO_DISCOVER_COMPANIES = True
MAX_COMPANIES_PER_PLATFORM = 150
MAX_PARALLEL = 12            # how many boards to check at once

GREENHOUSE_COMPANIES = ["datadog", "mongodb", "squarespace",
                        "justworks", "betterment", "peloton",
                        "duolingo", "stripe"]
LEVER_COMPANIES = ["spotify", "plaid", "palantir"]
ASHBY_COMPANIES = ["ramp", "openai", "notion", "runwayml"]
WORKDAY_COMPANIES = []       # entries look like ("zoom", "wd5", "Zoom")
SMARTRECRUITERS_COMPANIES = []
BAMBOOHR_COMPANIES = []


ADZUNA_APP_ID = ""
ADZUNA_APP_KEY = ""

# Sign up at developer.usajobs.gov: you give an email, they send a key.
USAJOBS_EMAIL = ""
USAJOBS_API_KEY = ""

# Get a key at console.anthropic.com. When set, Claude reads each
# borderline posting and answers: "Would a typical Bachelor's in CS
# graduate reasonably qualify?" Costs pennies/day with Haiku.
# Leave blank to use pure keyword scoring (free).
ANTHROPIC_API_KEY = ""
LLM_MODEL = "claude-haiku-4-5-20251001"
MAX_LLM_JOBS = 48            # cost/safety cap per run

# --- Smart filter tuning --------------------------------------------
# Which Simplify-list categories to include. The feed only has these:
# Software, AI/ML/Data, Hardware, Quant, Product (+ tiny ones).
# Empty list [] = include ALL. Roles outside the core software/data
# categories must have a clearly technical title to get through.
SIMPLIFY_CATEGORIES = []

MIN_TECH_SCORE = 4
MIN_ENTRY_SCORE = 2
YEARS_REJECT = 1     # reject any posting demanding >= this many years
                     # ("2-3 years" counts as 2, "1+ years" as 1).
                     # 1 = only true zero-experience jobs get through.
NEAR_MISS_MARGIN = 2         # how close a "no" must be for LLM rescue
DESCRIPTION_FETCH_LIMIT = 8  # full descriptions per company per run
EXTRA_EXCLUDE_KEYWORDS = []

# ==================================================================
# Cloud override: GitHub Actions (or any server) can supply the keys
# as environment variables, so no secrets ever live in this file.

def _env(name, fallback):
    value = os.environ.get(name, "").strip()
    return value or fallback

GMAIL_APP_PASSWORD = _env("GMAIL_APP_PASSWORD", GMAIL_APP_PASSWORD)
ADZUNA_APP_ID = _env("ADZUNA_APP_ID", ADZUNA_APP_ID)
ADZUNA_APP_KEY = _env("ADZUNA_APP_KEY", ADZUNA_APP_KEY)
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
USAJOBS_EMAIL = _env("USAJOBS_EMAIL", USAJOBS_EMAIL)
USAJOBS_API_KEY = _env("USAJOBS_API_KEY", USAJOBS_API_KEY)

SIMPLIFY_URL = ("https://raw.githubusercontent.com/SimplifyJobs/"
                "New-Grad-Positions/dev/.github/scripts/listings.json")
SIMPLIFY_CORE = {"Software", "Software Engineering",
                 "AI/ML/Data", "Data Science, AI & Machine Learning"}

BASE_DIR = Path(os.environ.get("JOB_ALERT_HOME")
                or (Path.home() / "job-alerts"))
SEEN_FILE = BASE_DIR / "seen_jobs.json"
DEAD_FILE = BASE_DIR / "dead_boards.json"
DEAD_BOARD_RETRY_DAYS = 7
LOG_FILE = BASE_DIR / "log.txt"


def log(message):
    BASE_DIR.mkdir(exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_json(url, timeout=25, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "personal-job-alerts-script",
               "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def parse_posted_on(text):
    """Workday-style '(Posted) 3 Days Ago' strings -> timestamp."""
    if not text:
        return None
    t = str(text).lower()
    now = datetime.now().timestamp()
    if "today" in t or "just posted" in t:
        return now
    if "yesterday" in t:
        return now - 86400
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return now - int(m.group(1)) * 86400
    return None


def strip_html(text):
    if not text:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _word(text, phrase):
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


# ================== SMART FILTER (keyword layer) ===================

TECH_QUALIFIERS = (
    "software", "cloud", "devops", "data", "security", "cyber",
    "cybersecurity", "network", "networking", "system", "systems",
    "platform", "infrastructure",
    "site reliability", "sre", "application", "applications",
    "full stack", "fullstack", "full-stack", "backend", "back end",
    "back-end", "frontend", "front end", "front-end", "mobile", "ios",
    "android", "web", "embedded", "firmware", "qa",
    "quality assurance", "test", "automation", "machine learning",
    "ml", "ai", "artificial intelligence", "database", "analytics",
    "business intelligence", "bi", "information technology", "it",
    "technical", "technology", "integration", "implementation",
    "support", "research", "computer", "api", "quantitative", "quant",
)
ROLE_NOUNS = ("engineer", "engineering", "developer", "programmer",
              "analyst", "scientist", "researcher", "administrator",
              "consultant", "technologist", "specialist")
STANDALONE_TECH_TITLES = (
    "swe", "sde", "member of technical staff", "technical staff",
    "technology development program", "engineering rotation",
    "engineering development program", "graduate engineer",
    "graduate developer", "web developer", "solutions engineer",
    "solutions architect",
)
TITLE_EXCLUDES = (
    "intern", "internship", "co-op", "coop", "senior", "sr", "staff",
    "principal", "lead", "director", "manager", "vp",
    "vice president", "chief", "executive", "recruiter", "recruiting",
    "talent acquisition", "human resources", "hr", "payroll",
    "finance", "accounting", "accountant", "audit", "tax", "attorney",
    "legal", "paralegal", "counsel", "nurse", "nursing", "physician",
    "dental", "clinical", "therapist", "pharmacist", "marketing",
    "sales", "account executive", "business development",
    "customer service", "cashier", "retail", "barista", "server",
    "waiter", "warehouse", "forklift", "driver", "delivery",
    "construction", "carpenter", "electrician", "plumber", "hvac",
    "mechanic", "janitor", "custodian", "housekeeping",
    "security guard", "teacher", "tutor", "phd",
)
STACK_WORDS = (
    "python", "java", "javascript", "typescript", "react", "node",
    "sql", "postgres", "mysql", "mongodb", "nosql", "aws", "azure",
    "gcp", "google cloud", "linux", "unix", "git", "docker",
    "kubernetes", "terraform", "jenkins", "ci/cd", "rest", "apis",
    "microservices", "networking", "tcp/ip", "firewall",
    "cybersecurity", "encryption", "vulnerability", "siem", "devops",
    "debugging", "automation", "selenium", "distributed systems",
    "machine learning", "deep learning", "pytorch", "tensorflow",
    "data pipeline", "etl", "spark", "hadoop", "kafka", "tableau",
    "power bi", "algorithms", "data structures", "object-oriented",
    "coding", "programming", "software development",
)
STACK_SUBSTRINGS = ("c++", "c#", ".net")

CS_FIELD_RE = re.compile(
    r"computer science|computer engineering|software engineering|"
    r"information systems|information technology|stem degree|"
    r"technical degree|engineering degree|related (technical )?field", re.I)
STRONG_ENTRY_RE = re.compile(
    r"new ?grad(uate)?|recent grad(uate)?|entry[ -]?level|"
    r"early[ -]?(in[ -]?)?career|graduate (program|scheme|hire|role)|"
    r"rotation(al)? program|development program|campus|"
    r"university grad(uate)?|college grad(uate)?|class of 20\d\d|"
    r"no (prior |previous )?(work |professional )?experience", re.I)
MED_ENTRY_RE = re.compile(r"\b(junior|jr|associate|apprentice|trainee)\b", re.I)
LEVEL_ONE_RE = re.compile(
    r"\b(engineer|developer|analyst|consultant|specialist|researcher|"
    r"scientist)\s+(i|1)\b", re.I)
BACHELOR_RE = re.compile(
    r"bachelor|\bb\.?s\.?\b.{0,30}(degree|computer|engineering)|"
    r"undergraduate degree|4[ -]year degree|college degree|"
    r"university degree|degree (required|preferred)|"
    r"or equivalent experience", re.I)
# Jobs whose description demands a Master's/PhD with no Bachelor's
# path are out of reach for a BS-only applicant.
GRAD_DEGREE_RE = re.compile(
    r"\bph\.?\s?d\b|doctorate|doctoral|\bmaster'?s?\b|\bm\.s\.|"
    r"\bmsc\b|graduate degree", re.I)
BACHELOR_OK_RE = re.compile(
    r"bachelor|\bb\.?s\.?\b|\bb\.?a\.?\b|undergraduate", re.I)

# Non-tech (mostly finance/IB) signals that pull the tech score DOWN.
NONTECH_WORDS = (
    "investment banking", "m&a", "mergers and acquisitions",
    "capital markets", "private equity", "capital raising", "cfa",
    "pitch book", "pitch books", "underwriting", "broker-dealer",
    "securities", "equity research", "wealth management",
    "portfolio companies", "financial models", "financial modeling",
    "series 7", "series 63", "gaap", "bookkeeping", "cold calling",
    "sales quota",
)

_YEAR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
               "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YEAR_NUM = r"(?:\d{1,2}|" + "|".join(_YEAR_WORDS) + r")"
YEARS_RE = re.compile(
    r"\b(" + _YEAR_NUM + r")\s*(?:\+\s*)?(?:-|–|—|to)\s*"
    r"(" + _YEAR_NUM + r")\s*\+?\s*y(?:ea)?rs?\b"
    r"|\b(" + _YEAR_NUM + r")\s*\+?\s*y(?:ea)?rs?\b", re.I)


def requires_advanced_degree(text):
    return bool(GRAD_DEGREE_RE.search(text)
                and not BACHELOR_OK_RE.search(text))


def title_excluded(title):
    t = title.lower()
    return any(_word(t, bad.lower())
               for bad in tuple(TITLE_EXCLUDES) + tuple(EXTRA_EXCLUDE_KEYWORDS))


def min_years_required(text):
    found = []
    for m in YEARS_RE.finditer(text):
        value = (m.group(1) or m.group(3)).lower()
        window = text[max(0, m.start() - 40):m.end() + 60].lower()
        if "experience" in window or "exp." in window:
            years = _YEAR_WORDS.get(value)
            if years is None:
                try:
                    years = int(value)
                except ValueError:
                    continue
            found.append(years)
    return min(found) if found else None


def tech_score(title, description):
    t, d = title.lower(), description.lower()
    score = 0
    has_noun = any(_word(t, n) for n in ROLE_NOUNS)
    has_qual = any(_word(t, q) for q in TECH_QUALIFIERS)
    if any(s in t for s in STANDALONE_TECH_TITLES) or (has_noun and has_qual):
        score += 4
    else:
        score += (1 if has_noun else 0) + (1 if has_qual else 0)
    hits = sum(1 for w in STACK_WORDS if _word(d, w))
    hits += sum(1 for w in STACK_SUBSTRINGS if w in d)
    score += min(hits, 6)
    if CS_FIELD_RE.search(d):
        score += 2
    nontech = sum(1 for w in NONTECH_WORDS if w in d)
    score -= min(nontech, 4)
    return score


def entry_score(title, description):
    t, d = title.lower(), description.lower()
    score = 0
    if STRONG_ENTRY_RE.search(t) or STRONG_ENTRY_RE.search(d):
        score += 3
    if MED_ENTRY_RE.search(t):
        score += 2
    if LEVEL_ONE_RE.search(t):
        score += 2
    yrs = min_years_required(d)
    if yrs == 0:                       # e.g. "0-2 years" welcomes 0 yoe
        score += 3
    if BACHELOR_RE.search(d):
        score += 2 if yrs is None else 1
    return score


def local_verdict(job):
    """'yes' = passes keyword filter, 'near' = close (LLM may rescue),
    'no' = clearly out."""
    title, desc = job["title"], job.get("description", "")
    if not title or title_excluded(title):
        return "no"
    if not job["needs_marker"]:
        # Simplify jobs are pre-vetted new-grad: confirm technical only.
        # Core software/data categories get a loose check; Hardware,
        # Quant, Product etc. need a clearly technical (CS) title.
        t = title.lower()
        strong = (any(s in t for s in STANDALONE_TECH_TITLES)
                  or (any(_word(t, n) for n in ROLE_NOUNS)
                      and any(_word(t, q) for q in TECH_QUALIFIERS)))
        if job.get("category") in SIMPLIFY_CORE:
            loose = (strong or any(_word(t, n) for n in ROLE_NOUNS)
                     or any(_word(t, q) for q in TECH_QUALIFIERS))
            return "yes" if loose else "no"
        return "yes" if strong else "no"
    if requires_advanced_degree(desc):
        return "no"
    yrs = min_years_required(desc.lower())
    if (yrs is not None and yrs >= YEARS_REJECT
            and not STRONG_ENTRY_RE.search(title + " " + desc)):
        return "no"
    ts, es = tech_score(title, desc), entry_score(title, desc)
    if ts >= MIN_TECH_SCORE and es >= MIN_ENTRY_SCORE:
        return "yes"
    if (ts >= MIN_TECH_SCORE - NEAR_MISS_MARGIN
            and es >= MIN_ENTRY_SCORE - NEAR_MISS_MARGIN):
        return "near"
    return "no"


# ============ Claude semantic filter (optional layer) ==============

LLM_QUESTION = (
    "You screen job postings for a person who just earned a Bachelor's "
    "degree in Computer Science (no full-time work experience). For each "
    "numbered posting decide: would this person reasonably qualify for "
    "and apply to it? qualified=true only if it is a technical role, "
    "entry-level requiring no prior work experience (0 years / new "
    "grad / degree-gated; reject if it demands 1+ years), and not an "
    "internship or senior position. Respond with ONLY a JSON array like "
    '[{"n":1,"qualified":true}] and nothing else.'
)


def _ask_claude(prompt):
    payload = {"model": LLM_MODEL, "max_tokens": 800,
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in data.get("content", []))


def llm_classify(jobs):
    """Ask Claude about each job. Returns {index: True/False}.
    Any failure returns {} so the keyword verdicts stand."""
    verdicts = {}
    if not ANTHROPIC_API_KEY or not jobs:
        return verdicts
    batch_size = 8
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start:start + batch_size]
        lines = [LLM_QUESTION, ""]
        for i, j in enumerate(batch, 1):
            lines.append(
                f"{i}. TITLE: {j['title']} | COMPANY: {j['company']} | "
                f"LOCATION: {', '.join(j['locations'])}\n"
                f"DESCRIPTION: {j.get('description', '')[:1200]}\n")
        try:
            reply = _ask_claude("\n".join(lines))
            reply = re.sub(r"```(json)?", "", reply).strip()
            for item in json.loads(reply):
                n = int(item.get("n", 0)) - 1
                if 0 <= n < len(batch):
                    verdicts[start + n] = bool(item.get("qualified"))
        except Exception as e:
            log(f"  Claude filter unavailable for one batch: {e}")
    return verdicts


# ------------------------- location -------------------------------

REMOTE_HINTS = ("remote", "work from home", "wfh", "hybrid", "anywhere",
                "distributed", "flexible", "virtual", "home based",
                "home-based", "home office", "telecommute")
NON_US_HINTS = ("canada", "toronto", "vancouver", "uk", "united kingdom",
                "london", "england", "europe", "emea", "apac", "asia",
                "india", "bangalore", "hyderabad", "australia", "germany",
                "berlin", "france", "ireland", "poland", "mexico",
                "brazil", "latam", "japan", "singapore", "philippines",
                "netherlands", "spain", "israel")


# Two-letter state codes are matched case-sensitively ("Boston, MA",
# "Miami FL", even "N.Y.") so that lowercase particles in foreign
# place names ("Ciudad de Mexico", "Ile-de-France") never trip the
# Delaware "DE", and "Tampa"/"Manhattan" never trip "PA"/"MA".
_STATE_CODE_RE = re.compile(
    "|".join(r"(?<![A-Za-z])" + r"\.?".join(code) + r"\.?(?![A-Za-z])"
             for code in EAST_COAST_STATES))


def location_matches(locations):
    if not LOCATION_KEYWORDS:
        return True
    for loc in locations or []:
        raw = str(loc)
        l = raw.lower()
        if any(_word(l, kw) for kw in LOCATION_KEYWORDS):
            return True
        if (_STATE_CODE_RE.search(raw)
                and not any(h in l for h in NON_US_HINTS)):
            return True
        if INCLUDE_REMOTE and any(h in l for h in REMOTE_HINTS):
            if not any(h in l for h in NON_US_HINTS):
                return True
    return False


def multi_location(text):
    return bool(re.search(r"\d+\s+locations", str(text).lower()))


# ==================== company auto-discovery =======================

DISCOVERY_PATTERNS = {
    "greenhouse": r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)",
    "lever": r"jobs\.lever\.co/([A-Za-z0-9_-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)",
    "workday": (r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/"
                r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"),
    "smartrecruiters": r"jobs\.smartrecruiters\.com/([A-Za-z0-9]+)/",
    "bamboohr": r"https?://([a-z0-9-]+)\.bamboohr\.com",
}


def discover_boards(simplify_data):
    """Extract every company career board the Simplify feed links to."""
    counts = {k: {} for k in DISCOVERY_PATTERNS}
    for j in simplify_data:
        url = j.get("url", "")
        for platform, pattern in DISCOVERY_PATTERNS.items():
            m = re.search(pattern, url)
            if m:
                key = m.groups() if platform == "workday" else m.group(1)
                counts[platform][key] = counts[platform].get(key, 0) + 1
    boards = {}
    for platform, found in counts.items():
        ranked = sorted(found, key=found.get, reverse=True)
        boards[platform] = ranked[:MAX_COMPANIES_PER_PLATFORM]
    return boards


# --------------------------- sources ------------------------------

def norm(company, title, locations, url, posted, description, source,
         needs_marker=True, sponsorship="", category=""):
    return {"company": company, "title": title, "locations": locations,
            "url": url, "posted": posted, "description": description,
            "source": source, "needs_marker": needs_marker,
            "sponsorship": sponsorship, "category": category}


def simplify_jobs(data):
    jobs = []
    for j in data:
        if not j.get("active") or not j.get("is_visible", True):
            continue
        if not j.get("url"):
            continue
        if SIMPLIFY_CATEGORIES and j.get("category") not in SIMPLIFY_CATEGORIES:
            continue
        jobs.append(norm(j.get("company_name", "Unknown"),
                         j.get("title", ""), j.get("locations") or [],
                         j["url"], j.get("date_posted") or None, "",
                         "Simplify new-grad list", needs_marker=False,
                         sponsorship=j.get("sponsorship", ""),
                         category=j.get("category", "")))
    return jobs


def prescreen(title, locs, posted, url, seen, cutoff, loc_ambiguous=False):
    if not url or url in seen or title_excluded(title):
        return False
    if not loc_ambiguous and not location_matches(locs):
        return False
    if posted and posted < cutoff:
        return False
    return True


def src_greenhouse(slug, seen, cutoff):
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs, fetched = [], 0
    for j in data.get("jobs", []):
        title = j.get("title", "")
        url = j.get("absolute_url")
        locs = [(j.get("location") or {}).get("name", "")]
        posted = parse_iso(j.get("first_published") or j.get("updated_at"))
        if not prescreen(title, locs, posted, url, seen, cutoff):
            continue
        description = ""
        if fetched < DESCRIPTION_FETCH_LIMIT and j.get("id"):
            try:
                detail = fetch_json("https://boards-api.greenhouse.io/"
                                    f"v1/boards/{slug}/jobs/{j['id']}")
                description = strip_html(detail.get("content", ""))
                posted = parse_iso(detail.get("first_published")) or posted
                fetched += 1
            except Exception:
                pass
        jobs.append(norm(slug.replace("-", " ").title(), title, locs, url,
                         posted, description,
                         f"{slug.title()} careers (Greenhouse)"))
    return jobs


def src_lever(slug, seen, cutoff):
    data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs = []
    for j in data:
        title = j.get("text", "")
        url = j.get("hostedUrl")
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        locs = [l for l in locs if l]
        created = j.get("createdAt")
        posted = (created / 1000.0) if created else None
        if not prescreen(title, locs, posted, url, seen, cutoff):
            continue
        pieces = [j.get("descriptionPlain") or strip_html(j.get("description"))]
        pieces += [strip_html(s.get("content", "")) for s in j.get("lists") or []]
        pieces.append(j.get("additionalPlain") or "")
        jobs.append(norm(slug.replace("-", " ").title(), title, locs, url,
                         posted, " ".join(p for p in pieces if p),
                         f"{slug.title()} careers (Lever)"))
    return jobs


def src_ashby(slug, seen, cutoff):
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        title = j.get("title", "")
        url = j.get("jobUrl") or j.get("applyUrl")
        locs = [j.get("location") or ""] + \
               [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        locs = [l for l in locs if l]
        posted = parse_iso(j.get("publishedAt"))
        if not prescreen(title, locs, posted, url, seen, cutoff):
            continue
        jobs.append(norm(slug.replace("-", " ").title(), title, locs, url,
                         posted,
                         strip_html(j.get("descriptionHtml")
                                    or j.get("descriptionPlain") or ""),
                         f"{slug.title()} careers (Ashby)"))
    return jobs


def src_workday(tenant, wd, site, seen, cutoff):
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    jobs, seen_paths, fetched = [], set(), 0
    for query in ("new grad", "entry level"):
        data = fetch_json(f"{base}/wday/cxs/{tenant}/{site}/jobs",
                          payload={"limit": 20, "offset": 0,
                                   "searchText": query,
                                   "appliedFacets": {}})
        for j in data.get("jobPostings", []):
            path = j.get("externalPath")
            title = j.get("title", "")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            url = f"{base}/{site}{path}"
            loc_text = j.get("locationsText", "")
            posted = parse_posted_on(j.get("postedOn"))
            ambiguous = multi_location(loc_text)
            if not prescreen(title, [loc_text], posted, url, seen, cutoff,
                             loc_ambiguous=ambiguous):
                continue
            description, locs = "", [loc_text]
            if fetched < DESCRIPTION_FETCH_LIMIT:
                try:
                    detail = fetch_json(f"{base}/wday/cxs/{tenant}/{site}{path}")
                    info = detail.get("jobPostingInfo") or {}
                    description = strip_html(info.get("jobDescription", ""))
                    locs = [info.get("location") or loc_text] + \
                           list(info.get("additionalLocations") or [])
                    fetched += 1
                except Exception:
                    pass
            if not location_matches(locs):
                continue
            jobs.append(norm(tenant.replace("-", " ").title(), title, locs,
                             url, posted, description,
                             f"{tenant.title()} careers (Workday)"))
    return jobs


def src_smartrecruiters(company, seen, cutoff):
    data = fetch_json("https://api.smartrecruiters.com/v1/companies/"
                      f"{company}/postings?limit=100")
    jobs, fetched = [], 0
    for j in data.get("content", []):
        title = j.get("name", "")
        pid = j.get("id")
        loc = j.get("location") or {}
        country = str(loc.get("country", "")).lower()
        if country and country not in ("us", "usa", "united states"):
            continue
        loc_str = ", ".join(x for x in (loc.get("city"), loc.get("region"))
                            if x)
        if loc.get("remote"):
            loc_str = (loc_str + " Remote").strip()
        url = f"https://jobs.smartrecruiters.com/{company}/{pid}"
        posted = parse_iso(j.get("releasedDate"))
        if not pid or not prescreen(title, [loc_str], posted, url, seen,
                                    cutoff):
            continue
        description = ""
        if fetched < DESCRIPTION_FETCH_LIMIT:
            try:
                detail = fetch_json("https://api.smartrecruiters.com/v1/"
                                    f"companies/{company}/postings/{pid}")
                sections = (detail.get("jobAd") or {}).get("sections") or {}
                description = " ".join(
                    strip_html(s.get("text", ""))
                    for s in sections.values() if isinstance(s, dict))
                fetched += 1
            except Exception:
                pass
        jobs.append(norm(company, title, [loc_str], url, posted, description,
                         f"{company} careers (SmartRecruiters)"))
    return jobs


def src_bamboohr(sub, seen, cutoff):
    data = fetch_json(f"https://{sub}.bamboohr.com/careers/list")
    jobs = []
    for j in data.get("result", []):
        title = j.get("jobOpeningName", "")
        jid = j.get("id")
        loc = j.get("location") or {}
        loc_str = ", ".join(x for x in (loc.get("city"), loc.get("state"))
                            if x)
        if str(j.get("isRemote", "")).lower() in ("1", "true", "yes"):
            loc_str = (loc_str + " Remote").strip()
        url = f"https://{sub}.bamboohr.com/careers/{jid}"
        if not jid or not prescreen(title, [loc_str], None, url, seen, cutoff):
            continue
        description = ""
        try:
            detail = fetch_json(f"https://{sub}.bamboohr.com/careers/{jid}/detail")
            description = strip_html(((detail.get("result") or {})
                                      .get("jobOpening") or {})
                                     .get("description", ""))
        except Exception:
            pass
        jobs.append(norm(sub.replace("-", " ").title(), title, [loc_str],
                         url, None, description,
                         f"{sub.title()} careers (BambooHR)"))
    return jobs


def src_muse(seen, cutoff):
    """The Muse public API - keyless, has an explicit Entry Level tag."""
    jobs = []
    for page in (0, 1, 2):
        params = ([("level", "Entry Level"), ("page", page)]
                  + [("location", loc) for loc in MUSE_LOCATIONS])
        data = fetch_json("https://www.themuse.com/api/public/jobs?"
                          + urlencode(params))
        for j in data.get("results", []):
            title = j.get("name", "")
            url = (j.get("refs") or {}).get("landing_page")
            locs = [l.get("name", "") for l in j.get("locations") or []]
            posted = parse_iso(j.get("publication_date"))
            if not prescreen(title, locs, posted, url, seen, cutoff):
                continue
            jobs.append(norm((j.get("company") or {}).get("name", "Unknown"),
                             title, locs, url, posted,
                             strip_html(j.get("contents", "")),
                             "The Muse (entry-level board)"))
        if not data.get("results"):
            break
    return jobs


def src_remotive(seen, cutoff):
    """Remotive public API - keyless remote-only board."""
    if not INCLUDE_REMOTE:
        return []
    jobs = []
    for cat in ("software-dev", "data"):
        data = fetch_json("https://remotive.com/api/remote-jobs?category=" + cat)
        for j in data.get("jobs", []):
            where = j.get("candidate_required_location", "") or "Worldwide"
            if not any(k in where.lower() for k in
                       ("usa", "united states", "worldwide", "anywhere",
                        "north america")):
                continue
            title = j.get("title", "")
            url = j.get("url")
            posted = parse_iso(j.get("publication_date"))
            locs = [f"Remote ({where})"]
            if not prescreen(title, locs, posted, url, seen, cutoff):
                continue
            jobs.append(norm(j.get("company_name", "Unknown"), title, locs,
                             url, posted, strip_html(j.get("description", "")),
                             "Remotive (remote jobs)"))
    return jobs


def src_remoteok(seen, cutoff):
    """RemoteOK public API - keyless remote tech board."""
    if not INCLUDE_REMOTE:
        return []
    data = fetch_json("https://remoteok.com/api")
    jobs = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        where = str(j.get("location", "")) or "Worldwide"
        if not any(k in where.lower() for k in
                   ("usa", "united states", "worldwide", "anywhere",
                    "north america", "americas", "")):
            continue
        title = j.get("position", "")
        url = j.get("url")
        posted = parse_iso(j.get("date"))
        locs = [f"Remote ({where})"]
        if not prescreen(title, locs, posted, url, seen, cutoff):
            continue
        jobs.append(norm(j.get("company", "Unknown"), title, locs, url,
                         posted, strip_html(j.get("description", "")),
                         "RemoteOK (remote jobs)"))
    return jobs


def src_usajobs(seen, cutoff):
    """US federal government jobs - free key from developer.usajobs.gov."""
    if not USAJOBS_API_KEY or not USAJOBS_EMAIL:
        return []
    params = urlencode({
        "JobCategoryCode": "2210;1550;0854",   # IT / CS / computer eng.
        "LocationName": ";".join(EAST_COAST_STATES.values()),
        "ResultsPerPage": 100, "SortField": "opendate",
        "SortDirection": "desc"})
    req = urllib.request.Request(
        "https://data.usajobs.gov/api/search?" + params,
        headers={"User-Agent": USAJOBS_EMAIL,
                 "Authorization-Key": USAJOBS_API_KEY,
                 "Host": "data.usajobs.gov"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    jobs = []
    items = ((data.get("SearchResult") or {}).get("SearchResultItems") or [])
    for it in items:
        d = it.get("MatchedObjectDescriptor") or {}
        title = d.get("PositionTitle", "")
        url = d.get("PositionURI")
        locs = [l.get("LocationName", "")
                for l in d.get("PositionLocation") or []]
        posted = parse_iso(d.get("PublicationStartDate"))
        if not prescreen(title, locs, posted, url, seen, cutoff):
            continue
        details = (d.get("UserArea") or {}).get("Details") or {}
        desc = " ".join([str(details.get("JobSummary", "")),
                         str(d.get("QualificationSummary", ""))])
        jobs.append(norm("US Government - " + (
                         (d.get("OrganizationName") or "Federal")),
                         title, locs, url, posted, strip_html(desc),
                         "USAJobs (federal government)"))
    return jobs


def src_adzuna():
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        return []
    jobs = []
    searches = ADZUNA_SEARCHES + ([""] if INCLUDE_REMOTE else [])
    for where in searches:
        params = {"app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
                  "results_per_page": 50, "max_days_old": 3,
                  "sort_by": "date", "category": "it-jobs",
                  "what_or": ("software developer engineer data analyst "
                              "security cloud graduate junior")}
        if where:
            params["where"] = where
        else:
            params["what_and"] = "remote"
        data = fetch_json("https://api.adzuna.com/v1/api/jobs/us/search/1?"
                          + urlencode(params))
        for r in data.get("results", []):
            if not r.get("redirect_url"):
                continue
            jobs.append(norm(
                (r.get("company") or {}).get("display_name", "Unknown"),
                r.get("title", ""),
                [(r.get("location") or {}).get("display_name", "")],
                r["redirect_url"], parse_iso(r.get("created")),
                strip_html(r.get("description", "")),
                "Adzuna (scans thousands of boards)"))
    return jobs


# --------------------------- gathering -----------------------------

def build_tasks(boards, seen, cutoff):
    tasks = []
    gh = list(dict.fromkeys(GREENHOUSE_COMPANIES + boards.get("greenhouse", [])))
    lv = list(dict.fromkeys(LEVER_COMPANIES + boards.get("lever", [])))
    ab = list(dict.fromkeys(ASHBY_COMPANIES + boards.get("ashby", [])))
    wd = list(dict.fromkeys([tuple(x) for x in WORKDAY_COMPANIES]
                            + boards.get("workday", [])))
    sm = list(dict.fromkeys(SMARTRECRUITERS_COMPANIES
                            + boards.get("smartrecruiters", [])))
    bh = list(dict.fromkeys(BAMBOOHR_COMPANIES + boards.get("bamboohr", [])))
    for c in gh:
        tasks.append((f"{c} (Greenhouse)",
                      lambda c=c: src_greenhouse(c, seen, cutoff)))
    for c in lv:
        tasks.append((f"{c} (Lever)", lambda c=c: src_lever(c, seen, cutoff)))
    for c in ab:
        tasks.append((f"{c} (Ashby)", lambda c=c: src_ashby(c, seen, cutoff)))
    for t in wd:
        tasks.append((f"{t[0]} (Workday)",
                      lambda t=t: src_workday(t[0], t[1], t[2], seen, cutoff)))
    for c in sm:
        tasks.append((f"{c} (SmartRecruiters)",
                      lambda c=c: src_smartrecruiters(c, seen, cutoff)))
    for c in bh:
        tasks.append((f"{c} (BambooHR)",
                      lambda c=c: src_bamboohr(c, seen, cutoff)))
    tasks.append(("The Muse", lambda: src_muse(seen, cutoff)))
    tasks.append(("Remotive", lambda: src_remotive(seen, cutoff)))
    tasks.append(("RemoteOK", lambda: src_remoteok(seen, cutoff)))
    tasks.append(("USAJobs", lambda: src_usajobs(seen, cutoff)))
    tasks.append(("Adzuna", src_adzuna))
    return tasks


def load_dead():
    if DEAD_FILE.exists():
        try:
            return json.loads(DEAD_FILE.read_text())
        except Exception:
            return {}
    return {}


def run_task(task):
    name, fn = task
    try:
        return name, fn(), None, False
    except urllib.error.HTTPError as e:
        return name, [], str(e), e.code == 404
    except Exception as e:
        return name, [], str(e), False


def gather_all(seen, cutoff):
    try:
        simplify_data = fetch_json(SIMPLIFY_URL, timeout=60)
    except Exception as e:
        log(f"  source skipped (Simplify list): {e}")
        simplify_data = []
    all_jobs = simplify_jobs(simplify_data)

    boards = (discover_boards(simplify_data)
              if AUTO_DISCOVER_COMPANIES else {})
    dead = load_dead()
    now_ts = datetime.now().timestamp()
    tasks = build_tasks(boards, seen, cutoff)
    muted = [t for t in tasks if dead.get(t[0], 0) > now_ts]
    tasks = [t for t in tasks if dead.get(t[0], 0) <= now_ts]
    log(f"Watching {len(tasks) - 1} company boards "
        f"({'auto-discovered + manual' if AUTO_DISCOVER_COMPANIES else 'manual'}) "
        f"+ Simplify, Adzuna, The Muse, Remotive, RemoteOK, USAJobs"
        + (f" | {len(muted)} dead board(s) muted" if muted else ""))

    errors, newly_dead = [], 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        for name, jobs, err, is_404 in pool.map(run_task, tasks):
            if is_404:
                dead[name] = now_ts + DEAD_BOARD_RETRY_DAYS * 86400
                newly_dead += 1
            elif err:
                errors.append(f"{name}: {err}")
            else:
                all_jobs.extend(jobs)
    if newly_dead:
        DEAD_FILE.write_text(json.dumps(dead))
        log(f"  {newly_dead} board(s) have no public job feed (404) - "
            f"muted for {DEAD_BOARD_RETRY_DAYS} days")
    for e in errors[:8]:
        log(f"  source skipped ({e})")
    if len(errors) > 8:
        log(f"  ...and {len(errors) - 8} more skipped")
    log(f"Checked {len(tasks) - len(errors) - newly_dead} source(s) OK, "
        f"{len(errors) + newly_dead} skipped, "
        f"{len(all_jobs)} raw postings collected")
    return all_jobs


# --------------------------- pipeline ------------------------------

def fingerprint(job):
    return f"{job['company'].strip().lower()}|{job['title'].strip().lower()}"


def find_new_jobs(all_jobs, seen, cutoff):
    passed, near, run_keys = [], [], set()
    for job in all_jobs:
        if not location_matches(job["locations"]):
            continue
        if job["posted"] and job["posted"] < cutoff:
            continue
        keys = {job["url"], fingerprint(job)}
        if keys & seen or keys & run_keys:
            continue
        verdict = local_verdict(job)
        if verdict == "no":
            continue
        run_keys |= keys
        (passed if verdict == "yes" else near).append(job)

    if ANTHROPIC_API_KEY:
        candidates = (passed + near)[:MAX_LLM_JOBS]
        verdicts = llm_classify(candidates)
        if verdicts:
            keep = []
            for i, job in enumerate(candidates):
                default = job in passed
                if verdicts.get(i, default):
                    keep.append(job)
            keep += passed[MAX_LLM_JOBS:]      # uncapped tail keeps old rule
            passed = keep
            log(f"Claude semantic filter reviewed {len(verdicts)} posting(s)")
    jobs = passed
    jobs.sort(key=lambda j: j["posted"] or 0, reverse=True)
    return jobs


def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen)[-50000:]))


# ---------------------------- email --------------------------------

def posted_ago(ts):
    if not ts:
        return "date not listed"
    hours = int((datetime.now().timestamp() - ts) // 3600)
    if hours < 1:
        return "just posted"
    if hours < 24:
        return f"posted {hours}h ago"
    return f"posted {hours // 24}d ago"


def bucket(title):
    t = title.lower()
    if any(k in t for k in ("data", "analytics", "machine learning", "ml ",
                            " ai", "business intelligence")):
        return "data"
    if any(k in t for k in ("security", "cyber", "network", "support", "qa",
                            "test", "it ", "systems", "help desk")):
        return "other"
    return "software"


def build_email(jobs, extra_count=0):
    groups = {"software": [], "data": [], "other": []}
    for j in jobs:
        groups[bucket(j["title"])].append(j)

    def html_section(heading, items):
        if not items:
            return ""
        rows = ""
        for j in items:
            loc = ", ".join(j["locations"]) or "Location not listed"
            spon = j.get("sponsorship", "")
            spon_html = f" &middot; {spon}" if spon and spon != "Other" else ""
            rows += ("<li style='margin-bottom:14px'>"
                     f"<a href='{j['url']}' "
                     "style='font-size:15px;font-weight:bold'>"
                     f"{j['company']} &mdash; {j['title']}</a><br>"
                     f"<span style='color:#555;font-size:13px'>{loc} &middot; "
                     f"{posted_ago(j['posted'])}{spon_html}</span><br>"
                     f"<span style='color:#999;font-size:11px'>via "
                     f"{j['source']}</span></li>")
        return f"<h3>{heading}</h3><ul style='padding-left:18px'>{rows}</ul>"

    more = (f"<p style='color:#777'>+{extra_count} more matches will arrive "
            "in the next scheduled emails.</p>" if extra_count else "")
    html_body = ("<html><body style='font-family:Arial,sans-serif'>"
                 f"<h2>{len(jobs)} entry-level tech posting(s)</h2>"
                 "<p style='color:#555'>Roles a CS grad can apply for, "
                 "newest first.</p>"
                 + html_section("&#128187; Software / CS", groups["software"])
                 + html_section("&#128202; Data / ML / Analytics",
                                groups["data"])
                 + html_section("&#128272; Security, IT &amp; other tech",
                                groups["other"])
                 + more +
                 "<p style='color:#999;font-size:12px'>Sent automatically by "
                 "job_alert.py on your Mac.</p></body></html>")

    lines = [f"{len(jobs)} entry-level tech posting(s):", ""]
    for j in jobs:
        loc = ", ".join(j["locations"]) or "Location not listed"
        lines.append(f"- {j['company']} - {j['title']}")
        lines.append(f"  {loc} | {posted_ago(j['posted'])} | via {j['source']}")
        lines.append(f"  {j['url']}")
        lines.append("")
    if extra_count:
        lines.append(f"+{extra_count} more matches arrive next run.")
    text = "\n".join(lines)

    subject = (f"{len(jobs)} entry-level tech posting(s) - "
               f"{datetime.now():%b %d, %I:%M %p}")
    if not jobs:
        subject = f"Job alerts ran - nothing new ({datetime.now():%b %d})"
        text = "The job checker ran successfully but found no new postings."
        html_body = f"<html><body><p>{text}</p></body></html>"
    return subject, html_body, text


def send_email(subject, html_body, text):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(EMAIL_RECIPIENTS)
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg, to_addrs=EMAIL_RECIPIENTS)


# ---------------------------- main ---------------------------------

def main():
    log("Run started")
    cutoff = datetime.now().timestamp() - HOURS_LOOKBACK * 3600
    seen = load_seen()
    all_jobs = gather_all(seen, cutoff)
    if not all_jobs:
        log("ERROR: every source failed - check your internet connection")
        sys.exit(1)

    jobs = find_new_jobs(all_jobs, seen, cutoff)
    log(f"Found {len(jobs)} new matching job(s) "
        f"(lookback {HOURS_LOOKBACK}h, remote="
        f"{'on' if INCLUDE_REMOTE else 'off'}, "
        f"semantic filter={'on' if ANTHROPIC_API_KEY else 'off'})")

    if "PASTE-APP-PASSWORD" in GMAIL_APP_PASSWORD:
        log("NOT SENT: paste your Gmail App Password into SETTINGS first.")
        for j in jobs[:10]:
            log(f"  would have sent: {j['company']} - {j['title']}")
        sys.exit(1)

    if not jobs and not EMAIL_WHEN_EMPTY:
        log("Nothing new - no email sent")
        return

    extra = max(0, len(jobs) - MAX_EMAIL_JOBS)
    jobs = jobs[:MAX_EMAIL_JOBS]
    subject, html_body, text = build_email(jobs, extra)
    try:
        send_email(subject, html_body, text)
    except Exception as e:
        log(f"ERROR sending email: {e}")
        sys.exit(1)

    for j in jobs:
        seen.add(j["url"])
        seen.add(fingerprint(j))
    save_seen(seen)
    log(f"Email sent to {', '.join(EMAIL_RECIPIENTS)} with {len(jobs)} job(s)"
        + (f" ({extra} held for next run)" if extra else ""))


if __name__ == "__main__":
    main()
