"""Edit these settings, then commit. Both alert profiles use this file."""

# THE LOCATION SWITCH: True = explicitly NY/NJ only (including remote NY/NJ).
# False = the East Coast states below plus eligible US/worldwide remote jobs.
# Generic "Remote", "US Remote", and hybrid jobs elsewhere do NOT pass True.
NY_NJ_ONLY = True
INCLUDE_REMOTE = True
EAST_COAST_STATES = (
    "ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "DE",
    "MD", "DC", "VA", "WV", "NC", "SC", "GA", "FL",
)

# A wider discovery window catches openings missed during outages.
HOURS_LOOKBACK = 168
MAX_REQUIRED_YEARS = 0       # Required experience; preferred experience is OK.
INCLUDE_UNKNOWN_EXPERIENCE = True  # Label uncertain requirements explicitly.
MAX_EMAIL_JOBS = 40
EMAIL_WHEN_EMPTY = False
DIGEST_MIN_HOURS = 6         # Queue remaining matches; don't drop at the email cap.
IMMEDIATE_HIGH_MATCH_ALERTS = False
HIGH_MATCH_SCORE = 90

MAX_PARALLEL = 6
MAX_REQUESTS_PER_RUN = 1600
MAX_WORKDAY_REQUESTS_PER_RUN = 120
MAX_PAGES_PER_SOURCE = 5     # Continue unfinished pagination on the next run.
DESCRIPTION_FETCH_LIMIT = 24
MAX_BOARDS_PER_RUN = 240     # Rotate fairly through ALL discovered boards.
HOST_INTERVAL_SECONDS = 1.0
WORKDAY_INTERVAL_SECONDS = 3.0
HTTP_ATTEMPTS = 3
MAX_INLINE_RETRY_SECONDS = 15
SOURCE_CACHE_SECONDS = 3600
DESCRIPTION_CACHE_SECONDS = 86400
REMOTIVE_CACHE_SECONDS = 21600  # One shared feed request, at most four/day.
STATE_RETENTION_DAYS = 180
SOURCE_FAILURE_WARNING_RATIO = 0.20
SOURCE_FAILURE_ERROR_RATIO = 0.75

# AI is optional and off by default. Rules, ranking and alerts work without it.
ENABLE_AI = False
LLM_MODEL = "claude-haiku-4-5-20251001"
MAX_LLM_JOBS = 12
MAX_LLM_JOBS_PER_DAY = 24

# More role families are also defined in job_alerts/profiles.py.
PREFERRED_SKILLS = {
    "tech": ("python", "java", "sql", "fastapi", "django", "machine learning", "cloud"),
    "finance": ("excel", "financial analysis", "sql", "valuation", "risk", "business analysis"),
}
# Optional constraints: None means no restriction. Unknown values are labeled.
MIN_ANNUAL_SALARY = {"tech": None, "finance": None}
REQUIRE_SPONSORSHIP = {"tech": False, "finance": False}
GRADUATION_YEAR = {"tech": 2026, "finance": 2027}

GREENHOUSE_COMPANIES = (
    "datadog", "mongodb", "squarespace", "justworks", "betterment", "peloton",
    "duolingo", "stripe", "jpmorganchase", "robinhood", "figma", "affirm",
    "coinbase", "oscar", "instacart", "vimeo", "flatironhealth", "etsy",
)
LEVER_COMPANIES = ("spotify", "plaid", "palantir")
ASHBY_COMPANIES = ("ramp", "openai", "notion", "runwayml", "mercury")
# Add known board identifiers here; discovery supplies hundreds more automatically.
WORKDAY_COMPANIES = ()       # (tenant, wd-number, career-site)
SMARTRECRUITERS_COMPANIES = ()
BAMBOOHR_COMPANIES = ()
