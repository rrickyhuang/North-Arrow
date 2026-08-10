# North Arrow

A personal, self-hosted job-aggregation pipeline, built for a Vancouver-based,
early-career designer's search but designed to be forked and pointed at your
own. It scrapes design-field job sources, normalizes and scores each posting
against personal criteria — including estimated SkyTrain commute and an LLM
read of genuine design-role fit — dedupes cross-source repeats, and delivers a
ranked daily shortlist as a markdown/HTML digest (email + file). A companion
local web cockpit turns that shortlist into an actual application-tracking
workspace.

The goal: stop manually sifting hundreds of postings to find the few worth
applying to.

> All personal preferences live in gitignored config files — the committed
> code contains no individual's data, so it's safe to clone and make your own.
> One thing to know going in: the scrapers themselves (PIBC, CSLA, municipal
> boards, etc.) are hardcoded to Vancouver design/planning sources — pointing
> this at a different city or field means writing new modules under
> `scrapers/` (see `scrapers/base.py`'s `fetch(cfg)` contract), not just
> editing config.

## How it works

```
scrape → parse/normalize → commute estimate → LLM enrich → score → store (SQLite) → dedup → digest
```

1. **Scrape** — each source module exposes `fetch(cfg) -> list[dict]` of raw postings.
2. **Parse** — CAD salary, role-type classifier, employment-type classifier, org classifier, location normalizer.
3. **Commute** — geocode (free, OpenStreetMap Nominatim) → nearest Expo/Millennium
   station → estimated minutes from your home station → score bucket. Jobs
   that reach the digest shortlist optionally get a real transit-time
   refinement from Google Distance Matrix (display-only, never affects
   scoring) — see `commute_precise.py`.
4. **Enrich** — one Claude Haiku call per job: fit signals (feed the score) + a
   display-only qualification verdict. Pre-filtered to skip jobs that will be
   disqualified; prior enrichment is reused so re-runs only pay for new jobs.
5. **Score** — weighted model, bonuses, soft penalties for role-type/employment-type
   red flags, one remaining hard disqualifier (out-of-metro, on-site). Commute +
   design-fit lead.
6. **Dedup** — group stored jobs across different sources by fuzzy title/company
   match, hide all but one "keeper" per group (direct/authoritative sources win
   over aggregators). Runs automatically after every scrape.
7. **Digest** — ranked shortlist to a markdown file and an HTML email.
8. **Cockpit** (`serve.py`) — a local Flask + HTMX web app for browsing the full
   DB, changing application status, drafting/revising cover letters, taking
   notes, and moving jobs across a pipeline board (Interested → Applied →
   Interviewing → Offer → Closed).

## Sources

Live: PIBC, CSLA, Indeed and LinkedIn (both via
[python-jobspy](https://github.com/speedyapply/JobSpy), which gets through
without a proxy at this search's volume), City of Vancouver, municipal Taleo
boards (Burnaby/New Westminster/Richmond), North Shore (District + City of
North Vancouver), Port Moody, Coquitlam, and one firm-direct source (Concrete
Cashmere Designs).

Confirmed scrapable but not yet built: Job Bank (jobbank.gc.ca), Eluta.ca,
BCJobs.ca — see open issues.

Investigated and rejected: CivicJobs.ca, Coroflot, IDIBC, We Work Remotely,
Dezeen (editorial RSS), Surrey (PeopleSoft login wall), BCSLA's own job board
(WAF-blocked), WorkBC (Angular SPA, no server-rendered listings), Workopolis
(rebranded SimplyHired, same Cloudflare wall as Indeed under a different
skin), SimplyHired.ca direct (403).

Beyond the fixed scrapers above, the `discover-jobs` Claude Code skill
(`.claude/skills/discover-jobs/`) does on-demand open-web discovery — browsing
small firm career pages and niche boards, checked against a self-growing,
gitignored journal (`discovery_journal.json`). Anything found is presented for
approval, then added through the same pipeline as `addjob.py` via `discover.py`.
Run it interactively with `/discover-jobs`.

## Setup

Requires Python 3.11+.

```bash
pip install -r requirements.txt

# Configure your search (gitignored — your details stay local):
cp config.example.yaml config.yaml      # Windows: copy config.example.yaml config.yaml
# then create .env with your secrets (see Secrets below)

# Edit config.yaml: keywords, home station/address, scoring weights, target firms, profile.
```

### Secrets

- `ANTHROPIC_API_KEY` — needed for LLM enrichment (Haiku) and, indirectly, for
  cover-letter drafting (`coverletter.py` shells out to the `claude` CLI on your
  Claude Code subscription instead, so it doesn't consume this key).
- Gmail delivery uses a Google **App Password** (requires 2FA on the account),
  not your normal password. Generate one at
  <https://myaccount.google.com/apppasswords>.
- `GOOGLE_MAPS_API_KEY` — optional, only needed for the precise (real transit
  time) commute refinement.

Both `.env` and `config.yaml` are gitignored and must be created from
`config.example.yaml` (there's no `.env.example`; see `config.py` for the
expected keys).

## Usage

```bash
python scrape.py --all --digest    # daily run: scrape all sources, score, email digest
python scrape.py --source pibc     # one source
python scrape.py --rescore         # re-rank stored jobs after a config change
python scrape.py --reenrich        # force fresh Haiku enrichment on every stored job (backfill a new field)
python scrape.py --dedup           # re-run cross-source duplicate detection only (auto-runs after every scrape anyway)
python scrape.py --check-links     # check a larger batch of postings for dead links (a small batch auto-runs after every scrape anyway)
python scrape.py --all --dry-run   # scrape + score but write nothing / call no LLM; ranked preview of what a real run would store

python show.py                     # terminal ranked list
python show.py 0                   # terminal detail for row #0
python show.py 0 --full            # same, untruncated description
python show.py --filter role_type=landscape_arch --filter stage!=denied   # filter by any Job field, AND-combined, != negates
python show.py --html              # full-DB HTML report, opens in browser

python serve.py                    # local web cockpit at http://127.0.0.1:5001
python serve.py --host 0.0.0.0     # also reachable from other devices on the same LAN (e.g. your phone), at http://<this-pc's-LAN-ip>:5001

python digest.py [--no-email|--stdout]
python addjob.py                   # paste in a job you found yourself — same scoring/enrichment pipeline
python addjob.py --edit <row-# or id>   # fix a previously manually-added job (forces re-enrichment)
python coverletter.py <row-# or id> [--notes "..."]     # draft a cover letter for a stored job
python coverletter.py revise <row-# or id> ["..."]      # interactively tweak a saved letter (loops; backs up to .md.bak)
python mark.py <row-# or id> [<row-# or id> ...] <applied|interviewing|offer|denied|withdrawn|interested|not-interested|seen> [--clear]
python help.py                     # consolidated command reference (pulled from each script's docstring)
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Unit tests for the deterministic pieces — scoring, location/salary/dedup
parsing, commute bucket logic, config validation, the DB layer (in-memory
SQLite), and scraper HTML/JSON parsing (mocked fixtures, no network). No
coverage yet for the LLM-enrichment calls or the live scrapers themselves.

## Key files

- `config.yaml` — all tunable settings + personal `profile` (gitignored; copy from `config.example.yaml`)
- `config.py` — loads config + `.env` secrets
- `models.py` — the `Job` dataclass
- `db.py` — SQLite persistence (+ schema migration)
- `commute.py` / `transit_data.py` — free transit-commute estimate; station coords
- `commute_precise.py` — optional real transit-time refinement (Google Distance Matrix), display-only, never affects scoring
- `parsers/` — `salary_cad.py`, `role_classifier.py`, `employment_classifier.py`, `org_classifier.py`, `normalize.py`
- `scrapers/` — `base.py` (HTTP/throttle/bot-wall), `_jobspy_common.py` (shared JobSpy plumbing), one `source_*.py` module per board/site
- `enrichment.py` — Claude Haiku call (fit signals + qualification verdict)
- `scorer.py` — weighted scoring model
- `dedup.py` — cross-source duplicate detection (fuzzy match via `rapidfuzz`)
- `linkcheck.py` — confirms via a live HTTP request whether a posting's URL is still up; confirmed-dead postings drop into their own hidden cockpit group
- `digest.py` — markdown + HTML email delivery
- `html_render.py` — shared HTML for email, the static report, and the cockpit
- `show.py` — read-only terminal/browser viewer
- `serve.py` — local Flask + HTMX web cockpit (browse, status, cover letters, notes, pipeline board)
- `addjob.py` — manual job intake, same scoring/enrichment pipeline as scraped jobs
- `coverletter.py` — drafts/revises cover letters via the `claude` CLI (not the API)
- `company_research.py` — one-time web-research blurb on the hiring company, cached per job and fed into cover-letter drafts
- `mark.py` — sets a job's application status/stage
- `scrape.py` — CLI entry point and pipeline orchestration
- `run_daily.bat` — Windows Task Scheduler entry point (`scrape.py --all --digest`, daily 9pm)
- `help.py` — prints a consolidated command reference from each script's docstring

## Decisions

- **No separate web framework beyond Flask/HTMX for the cockpit; no build step.** Mirrors a sibling apartment-hunter app's conventions (CDN libs, HTMX partial swaps) so the two tools feel identical.
- **LinkedIn and Indeed are scraped via [JobSpy](https://github.com/speedyapply/JobSpy), not a hand-rolled scraper.** A prior requests/BeautifulSoup approach got Cloudflare-walled on both; JobSpy gets through with no proxy at this search's volume. LinkedIn is the more rate-limit-sensitive of the two — kept at a modest `jobspy.results_wanted`.
- **Commute scoring uses free Nominatim + hard-coded station data — ranking never depends on a paid API.** Only Expo/Millennium lines count. An optional, display-only refinement (`commute_precise.py`) calls Google Distance Matrix for real door-to-door time, but only for jobs that already made the digest shortlist, never feeding back into the score.
- **Scope is all design fields, core-first.** Core (urban/landscape/planning/civic/architecture) = role score 1.0; other design (interior/graphic/industrial/digital) = 0.75; design-adjacent = 0.55; ops-design (space planning/facilities at logistics/retail/manufacturing companies) = 0.65. See `scoring.role_type_scores`.
- **Qualification is a separate, display-only axis** (seniority/credentials/verdict) — shown but never changes the fit score or ranking directly; it does apply a soft penalty (below) so reach-tier roles don't outrank applyable ones.
- **Admin-heavy, `role_type=admin`, `role_type=drafting_only`, and non-full-time employment are all soft multiplier penalties**, not disqualifiers — a job classified this way can still surface if it's otherwise a strong match. **Out-of-metro, on-site is the one remaining hard disqualifier** (score forced to 0) since there's no "good fit despite it" case for a posting that isn't commutable at all.
- **Metro detection covers the full Metro Vancouver Regional District**, not just the City of Vancouver (`parsers/normalize.py`'s `_METRO` list).
- **Salary floor is soft** (docks score, never disqualifies).
- **Cross-source duplicates are hidden, not deleted** — `dedup.py` marks all but one "keeper" per fuzzy-matched group `duplicate_of`, same hide-by-default treatment as `dismissed`. `dedup.source_priority` in config ranks direct/authoritative boards above aggregators.
- **The digest always shows current best matches, not a diff.** Every run re-lists all jobs at/above `min_score_for_digest`, ranked highest-first. `is_new` is a highlight only, clearing once an email actually sends.
- **The digest is application-aware.** Jobs in an active pipeline stage (`applied`/`interviewing`/`offer`) are pulled into a compact tracker instead of the suggestion groups; closed stages (`denied`/`withdrawn`) drop out entirely.
- **Cover letters deliberately don't use the Anthropic API.** `coverletter.py` shells out to the `claude` CLI (Claude Code subscription, not metered tokens) since it's low-frequency and human-triggered, unlike the high-volume unattended Haiku enrichment calls.
- **`show.py` stays strictly read-only** — all status changes go through `mark.py` or the cockpit explicitly.
- **Secrets and personal config are gitignored**; `.example` templates are committed, so the repo is safe to keep public.

## Glossary

- **Fit score** — 0..1 weighted match to preferences (commute, role tier, design autonomy, mixed role, salary, role quality) + bonuses. Drives ranking.
- **Qualification** — display-only verdict (`qualified` / `stretch` / `reach` / `overqualified`) of whether you meet the posting's stated requirements. Independent of the fit score, though it does apply a soft ranking penalty for stretch/reach.
- **Disqualifier** — a hard rule forcing score to 0. Only one remains: out-of-metro, on-site.
- **Near miss** — a digest section listing below-threshold jobs so a thin day still shows something.

## License

MIT — see [LICENSE](LICENSE).
