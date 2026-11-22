# Job alerts

Python job alerts for tech/CS and finance/business searches. Both profiles share
one collection pipeline, then receive separate ranked email digests. The matching
is rule-based by default; optional Claude review is disabled and is not required.
The project uses Python's standard library, with no paid dependencies.

## The New York / New Jersey switch

Open **[settings.py](settings.py)** and change this single boolean:

```python
NY_NJ_ONLY = True
```

It is **on by default for both profiles**. Only explicitly identified New York or
New Jersey locations pass. A posting offering several locations displays only its
matching locations. Remote roles explicitly based in NY/NJ pass; generic “Remote”,
“US Remote”, worldwide jobs, and hybrid jobs elsewhere do not.

Set it to `False` to restore the configured East Coast states plus remote postings
that explicitly permit US/worldwide applicants. Ambiguous or foreign locations
are rejected conservatively. This can exclude an eligible remote job whose
location field does not identify NY/NJ; it is intentional in strict mode.

## Coverage and matching

- Software, data, AI/ML/MLOps, cloud, security, QA, IT/application support,
  implementation, business systems, technology risk, product and related roles.
- Finance, banking, credit, risk, compliance, business analytics, operations,
  product, project coordination, pricing, strategy and related roles.
- Sources: Simplify, Greenhouse, Lever, Ashby, Workday, SmartRecruiters, BambooHR,
  The Muse, Remotive, RemoteOK, and optional Adzuna/USAJobs.
- Company boards are discovered automatically and rotated fairly; the discovery
  list is not cut down to a fixed top 150 per platform.
- Full Greenhouse descriptions are obtained in one request. Paginated sources
  resume unfinished work. Request budgets and provider cooldowns bound each run.
- Mandatory experience is distinct from preferred experience. “Campus” in a
  benefits paragraph cannot override a five-year requirement. “Member of Technical
  Staff” and “SQL Server Developer” are not rejected as senior/unrelated titles.
- `MAX_REQUIRED_YEARS = 0` preserves the existing zero-experience requirement.
  You can raise it deliberately. Unknown experience is clearly labeled for review;
  it is not represented as confirmed eligibility.
- Duplicate copies are merged by canonical job URL, preserving job ID query
  parameters. Different requisitions with the same company/title remain distinct.
  Different aggregator URLs may still represent the same opening; title-only
  suppression is deliberately avoided because it loses legitimate jobs.
- Scores explain role, location, experience and skill matches. They are heuristic
  rankings, not probabilities of getting hired. Salary/sponsorship are marked
  unknown when unavailable. Employer requirements remain authoritative.

Settings for skills, graduation year, salary, sponsorship, experience, cadence and
optional AI live in `settings.py`; profile vocabularies live in
`job_alerts/profiles.py`.

## Run locally

Python 3.12 is used in CI. No pip install is needed.

```bash
python -m unittest discover -s tests -v
python -m job_alerts.runner --input tests/sample_jobs.json --dry-run
python -m job_alerts.runner --profile all --dry-run
python -m job_alerts.runner --profile all --send
```

Preview is the default. `--dry-run` never sends email, invokes AI, marks a job sent,
or saves delivery state. Live previews may warm the disposable source cache.
Preview HTML, JSON matches and source health are written to `output/` (gitignored).
`python job_alert.py --send` and `python job_alert_finance.py --send` are compatible
entry points; use the shared runner to avoid duplicating collection.

## Email configuration

Use these GitHub Actions secrets/environment variables:

| Variable | Purpose |
|---|---|
| `GMAIL_ADDRESS` | Existing sender address |
| `GMAIL_APP_PASSWORD` | Gmail app password; already configured in the existing workflow |
| `TECH_EMAIL_RECIPIENTS` | Comma-separated tech recipients |
| `FINANCE_EMAIL_RECIPIENTS` | Comma-separated finance/business recipients |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Optional Adzuna access |
| `USAJOBS_EMAIL`, `USAJOBS_API_KEY` | Optional federal jobs access |
| `ANTHROPIC_API_KEY` | Optional; only used when `ENABLE_AI = True` |

Sender and recipient addresses are loaded exclusively from environment variables
or GitHub Actions secrets. No personal address defaults remain in the current
source. Removing addresses from current files does not erase old Git history.

Recipients receive separate messages. Failed recipients are retried without
resending to recipients already accepted by the mail server. SMTP acceptance is
not proof of inbox delivery. Delivery history is checkpointed after every recipient;
a crash between SMTP acceptance and the state write can still cause a duplicate.

## Scheduling and state

GitHub Actions collects hourly at minute 17; schedules are best-effort. Default
digests are at least six hours apart. `MAX_EMAIL_JOBS = 40`; overflow stays queued.
Set `IMMEDIATE_HIGH_MATCH_ALERTS = True` to send matches above `HIGH_MATCH_SCORE`
between digests. It is off by default.

The workflow **Run workflow** button defaults to preview only. Uncheck `dry_run`
to send. Scheduled runs send automatically. Existing Gmail and Adzuna secrets are
reused. No new external scheduler or paid AI is required.

`state/alerts_v2.json` records per-recipient delivery fingerprints, queued matches,
pagination, cooldowns and application statuses. It is committed atomically by the
workflow; save/push errors fail visibly. The old `seen_jobs*.json` histories migrate
by URL on first use; broad company/title fingerprints are discarded. They remain
in Git as a migration source, so an upgrade does not erase alert history.

**The repository is public.** State and any application status committed through
the workflow are public, even though recipient addresses are not included in v2
state. For private application tracking, set `JOB_ALERT_HOME` to a private local
directory and do not commit that file. Caches and previews are gitignored.

Source caches are disposable and reused via GitHub Actions cache. One shared
Remotive feed is refreshed no more than four times a day while cache persists.
HTTP 429/503 Retry-After values are respected, with longer waits deferred to later
runs. Cold/evicted caches can require additional requests. Missing API keys are
reported as disabled sources, not successful searches. Partial failures and budget
deferrals appear in the Actions summary; predominantly failed searches exit nonzero.

## Track applications

```bash
python track_job.py tech "https://employer.example/jobs/123" saved
python track_job.py tech "https://employer.example/jobs/123" applied
```

Statuses: `saved`, `applied`, `interviewing`, `rejected`, `dismissed`, `reset`.
Applied/interviewing/rejected/dismissed jobs are suppressed from future alerts.
Use `reset` to remove a status. This records your status; it never submits an
application. For GitHub-managed state, use the workflow's optional `track_profile`,
`track_url`, and `track_status` fields (this is public as noted above).

## Validation

Tests cover labeled posting examples, strict/broad locations, title exclusions,
required/preferred experience, duplicate merging, pagination/resumption, retries,
cache reuse, partial source failures, HTML escaping, strict AI JSON parsing,
legacy migration, recipient-specific retries, overflow, tracking and dry runs.
These regression examples are not a measured real-world accuracy benchmark.

## Development

I defined the requirements and used AI assistance to build and review this project.
See the source health summary to assess coverage; no job collector can guarantee
every available opening or that an employer will accept a particular candidate.
