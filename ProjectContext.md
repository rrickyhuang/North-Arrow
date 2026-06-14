# ProjectContext — JobHunter

Timeless reference for how JobHunter works. For current state and open work, see [Status.md](Status.md).

## Purpose

A personal, local job-aggregation pipeline for a Vancouver-based, early-career
designer (Ricky). It scrapes design-field job sources, normalizes and scores each
posting against personal criteria — including estimated SkyTrain commute and an
LLM read of genuine design-role fit — and delivers a ranked daily shortlist as a
markdown/HTML digest (email + file). Goal: surface the few roles worth applying to
without manually sifting hundreds.

## Pipeline

```
scrape → parse/normalize → commute estimate → LLM enrich → score → store (SQLite) → digest
```

1. **Scrape** — each source module exposes `fetch(cfg) -> list[dict]` of raw postings.
2. **Parse** — CAD salary, role-type classifier, org classifier, location normalizer.
3. **Commute** — geocode (free, OpenStreetMap Nominatim) → nearest Expo/Millennium
   station → estimated minutes from Commercial-Broadway → score bucket.
4. **Enrich** — one Claude Haiku call per job: fit signals (feed the score) + a
   display-only qualification verdict. Pre-filtered to skip jobs that will be
   disqualified; prior enrichment is reused so re-runs only pay for new jobs.
5. **Score** — weighted model, hard disqualifiers, bonuses. Commute + design-fit lead.
6. **Digest** — ranked shortlist to a markdown file and an HTML email.

## Key files

- `config.yaml` — all tunable settings + personal `profile` (gitignored; copy from `config.example.yaml`)
- `config.py` — loads config + `.env` secrets
- `models.py` — the `Job` dataclass
- `db.py` — SQLite persistence (+ schema migration)
- `commute.py` / `transit_data.py` — free transit-commute estimate; station coords
- `parsers/` — `salary_cad.py`, `role_classifier.py`, `org_classifier.py`, `normalize.py`
- `scrapers/` — `base.py` (HTTP/throttle/bot-wall), `source_pibc.py`, `source_csla.py`, `source_indeed.py` (shelved)
- `enrichment.py` — Claude Haiku call (fit + qualification)
- `scorer.py` — weighted scoring model
- `digest.py` — markdown + HTML email delivery
- `html_render.py` — shared HTML for email and the browser report
- `show.py` — read-only viewer (terminal list/detail; `--html` browser report)
- `scrape.py` — CLI entry point and pipeline orchestration

## Commands

```
python scrape.py --all --digest   # daily run: scrape all sources, score, email digest
python scrape.py --source pibc     # one source
python scrape.py --rescore         # re-rank stored jobs after a config change
python show.py                     # terminal ranked list
python show.py 0                   # terminal detail for row #0
python show.py --html              # full-DB HTML report, opens in browser
python digest.py [--no-email|--stdout]
```

## Settled decisions

- **No Flask/web server.** Output is a markdown/HTML digest + a static HTML report.
- **No LinkedIn/Indeed scraping.** Both are Cloudflare-blocked (HTTP 403); kept only as graceful-skip. Manual submission is the intended path for those (see Status open items).
- **Commute uses free Nominatim + hard-coded station data.** No Google Maps key/billing. Only Expo/Millennium lines count.
- **Scope is all design fields, core-first.** Core (urban/landscape/planning/civic/architecture) = role score 1.0; other design (interior/graphic/industrial/digital) = 0.75; design-adjacent = 0.55. See `scoring.role_type_scores`.
- **Qualification is a separate, display-only axis.** Seniority/credentials/verdict are shown but never change the fit score or ranking.
- **Admin-heavy = soft 0.4× penalty**, not a disqualifier (stays visible, ranks low).
- **Salary floor $60k is soft** (docks score, never disqualifies).
- **Secrets and personal config are gitignored.** `.env` (API key, Gmail app password) and `config.yaml` (personal profile/preferences) never committed; `.example` templates are. Repo is safe to make public.
- **Live sources are PIBC + CSLA only.** Investigated and rejected for a Vancouver design search: Indeed/LinkedIn (blocked), Coroflot (US-centric, no real Vancouver jobs), IDIBC (JS-walled embed), We Work Remotely (noise + region-locked), Dezeen (editorial RSS).

## Repo

Local git repo; remote `https://github.com/rrickyhuang/JobHunter` (private, **not pushed yet**).

## Glossary

- **Fit score** — 0..1 weighted match to preferences (commute, role tier, design autonomy, mixed role, salary, role quality) + bonuses. Drives ranking.
- **Qualification** — display-only verdict (`qualified` / `stretch` / `reach` / `overqualified`) of whether Ricky meets the posting's requirements. Independent of fit score.
- **Disqualifier** — a hard rule forcing score to 0 (admin/drafting role, out-of-metro on-site).
- **Near miss** — a digest section listing below-threshold jobs so a thin day still shows something.
