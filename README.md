# JobHunter

A personal, self-hosted job-aggregation pipeline for design-field roles
(urban design, landscape architecture, public realm, planning). It scrapes a
set of field-specific sources, normalizes and de-duplicates the postings,
scores each one against *your* criteria — including estimated transit commute
from your home station — and delivers a ranked daily shortlist as a markdown
digest and/or email.

The goal: stop manually sifting 200 postings to find the 4 that actually fit.

> This is a personal tool. All personal preferences live in gitignored config
> files — the committed code contains no individual's data.

## How it works

```
Sources ─▶ Parse/Normalize ─▶ LLM enrich ─▶ Dedup ─▶ Score ─▶ SQLite ─▶ Digest
```

- **Sources** — Indeed.ca and LinkedIn (via [python-jobspy](https://github.com/speedyapply/JobSpy),
  which handles both without a proxy at this search's volume), Archinect, PIBC,
  CSLA, Idealist, Dezeen (RSS), and direct firm career pages.
- **Parse/Normalize** — CAD salary extraction, role-type and org-type
  classification, location normalization, and a free transit-commute estimate
  (geocode via OpenStreetMap Nominatim → nearest station → estimated ride time).
- **LLM enrichment** — an optional Claude (Haiku) pass reads each description for
  the things keywords miss: design autonomy, whether the role genuinely mixes
  design with coordination, admin-heaviness, etc.
- **Score** — a weighted, configurable model. Commute and genuine design-role fit
  lead by default. Role-type red flags (pure admin/drafting) are soft multiplier
  penalties, not hard zeros, so a strong match can still surface. Out-of-metro,
  on-site postings are still a hard disqualifier (score forced to 0) since
  there's no "strong fit despite it" case for a job that isn't commutable at all.
- **Digest** — a ranked markdown file and/or an emailed shortlist.

## Setup

Requires Python 3.11+.

```bash
pip install -r requirements.txt

# Configure your search (gitignored — your details stay local):
cp config.example.yaml config.yaml      # Windows: copy config.example.yaml config.yaml
cp .env.example .env                     # then edit both

# Edit config.yaml: keywords, home station, scoring weights, target firms.
# Edit .env: ANTHROPIC_API_KEY and Gmail app password (if emailing).
```

### Secrets

- `ANTHROPIC_API_KEY` — only needed if LLM enrichment is enabled.
- Gmail delivery uses a Google **App Password** (requires 2FA on the account),
  not your normal password. Generate one at
  <https://myaccount.google.com/apppasswords>.

Both `.env` and `config.yaml` are gitignored and must be created from their
`.example` templates.

## Usage

```bash
python scrape.py            # run all enabled sources, score, write/send digest
python scrape.py --source indeed
```

*(CLI and scheduler are added incrementally — see the build plan.)*

## Configuration

Everything tunable lives in `config.yaml`:

- `search_queries` — keywords, location, exclusions
- `commute` — home station, acceptable transit lines, time→score buckets
- `scoring.weights` — relative importance of each factor
- `scoring.penalties` — soft multiplier docks for role-type red flags (admin, drafting-only, etc.)
- `disqualifiers` — the one remaining hard filter: out-of-metro, on-site postings
- `profile` — a description of the candidate, fed to the LLM enrichment prompt
- `delivery` — markdown / email options and score thresholds

## Project status

Built in phases: (1) schema + DB ✅, (2) first scraper + parsers + commute,
(3) scorer, (4) enrichment, (5) more sources, (6) firm-direct scrapers,
(7) dedup + digest + scheduler.

## License

MIT — see [LICENSE](LICENSE).
