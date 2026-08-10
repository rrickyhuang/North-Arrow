---
name: discover-jobs
description: Browse/search the open web for job postings from sources the existing scrapers don't cover (small firm career pages, niche boards, local news), add approved finds through the same pipeline as a manual job entry, and maintain a persistent journal of sources checked. Use when the user asks to discover new jobs from the open web, check/grow discovery sources, or invokes /discover-jobs.
---

# Open-web job discovery

Finds postings the registered scrapers (`scrape.py`'s `SOURCES`) can't reach, and
routes anything approved through the exact same pipeline as `addjob.py` (parse →
commute → enrich → score → store → dedup) via `discover.py`. See issue #11 for the
original spec. This process is interactive by design — nothing gets added to the
DB without you approving it first.

## 1. Load context

Read the search/profile context so you can judge relevance later:

```
python -c "import config, json; c = config.load_config(); print(json.dumps({'keywords': c['search_queries']['keywords'], 'location': c['search_queries']['location'], 'exclude_keywords': c['search_queries'].get('exclude_keywords', []), 'profile': c.get('profile', {}), 'discovery': c.get('discovery', {})}))"
```

Note `discovery.default_cadence_days` and `discovery.max_new_sources_per_run`
(fall back to 14 and 2 respectively if the section is missing from the user's
`config.yaml` — it's optional).

Read `discovery_journal.json` at the repo root. If it doesn't exist, create it
seeded with this starter list (stamp each entry's `cadence_days` from
`discovery.default_cadence_days` unless noted otherwise below, `last_checked:
null`, `last_found_count: 0`):

```json
{
  "sources": [
    {"url": "https://archinect.com/jobs", "name": "Archinect Jobs",
     "notes": "niche design job board — 403s to WebFetch, try the Browser tool instead"},
    {"url": "https://www.civicinfo.bc.ca/careers", "name": "CivicInfo BC — BC Local Government Job Posting Service",
     "notes": "487+ live postings, dated, filterable by professional category. 403s to WebFetch — use the Browser tool. Keyword search AND-matches the whole phrase, so search one word at a time (e.g. 'landscape', 'planner'), not multi-word phrases. Best value: BC municipalities outside the existing scraper list."},
    {"url": "https://www.bcsla.org/marketplace/job-listings", "name": "BCSLA job listings",
     "notes": "WAF-blocked to automated scrapers per README — a manual/browser-driven check here is fine since it's occasional, not a bot crawl pattern"},
    {"url": "https://www.bcsla.org/firms", "name": "BCSLA firm directory",
     "notes": "not a job source itself, and low-yield so far (two firm leads chased from here had no monitorable careers page) — browse occasionally to find small BC landscape-architecture firms not yet in this journal"},
    {"url": "https://raic.org/careers", "name": "RAIC (Royal Architectural Institute of Canada) job board",
     "cadence_days": 21, "notes": "legitimate board, but low volume for BC specifically — longer cadence"},
    {"url": "https://dialogdesign.ca/careers", "name": "DIALOG",
     "cadence_days": 30},
    {"url": "https://mg-architecture.ca/studio/careers/", "name": "Michael Green Architecture",
     "cadence_days": 30},
    {"url": "https://a49.com/Careers", "name": "Architecture49 / A49",
     "cadence_days": 30}
  ]
}
```

Firm-direct career pages (DIALOG, MGA, A49) are structurally lower-yield than
association/civic boards — no per-posting dates, frequently empty — so they're
seeded at a longer 30-day cadence rather than dropped, since an empty page
today doesn't mean it stays empty. Prefer boards over firm-direct pages when
proposing new sources in step 7.

## 2. Prioritize sources

A source is **due** if `last_checked` is null, or `last_checked + cadence_days <=
today`. Skip anything with `cadence_days: null` or a `notes` field indicating it's
not worth rechecking (e.g. "skip", "dead link"). List the due sources to the user
before proceeding so they know what this run will touch.

## 3. Check each due source

For a direct URL, use WebFetch on the career/listings page. For a directory/search
target (like the BCSLA firm directory), use WebFetch or WebSearch as appropriate to
browse it. For each posting found, judge relevance against `keywords` /
`exclude_keywords` / `profile` before treating it as a candidate — don't propose
postings that are clearly outside the field or excluded by keyword.

**Prefer boards that expose a per-posting date over firm-direct career pages
when there's a choice.** A posted/closing date is a free, structural staleness
signal a bare "current openings" list doesn't have — it's how you tell a real
live posting from something that's just been sitting there. Note each
candidate's posted/closing date when available and flag anything already past
its closing date as stale rather than proposing it.

**If WebFetch 403s or a page looks suspiciously empty**, try the same URL with
the Browser tool (`preview_start` with `{url}}`, then `get_page_text`/`read_page`)
before concluding a source is dead — several sources in this journal (see
CivicInfo BC's notes) are bot-blocked to plain HTTP fetches but load fine in an
actual browser. Only mark a source empty/dead in the journal once you've
confirmed it via whichever method actually got through, not just on a 403.

Be conservative about what counts as "found": if a page's content is ambiguous,
paywalled, JS-rendered with nothing in the fetched HTML, or you're not confident a
posting is real/current, say so rather than guessing.

## 4. Present candidates for approval

After checking all due sources this run, show the user everything found in one
list: title, company, url, one-line reasoning for why it's plausibly relevant. Ask
which to add — all, none, or a subset. **Do not add anything before this
approval step.**

## 5. Add approved postings

For each approved posting, write a JSON file to the scratchpad directory:

```json
{"title": "...", "company": "...", "url": "...", "location": "...", "description": "..."}
```

Then run:

```
python discover.py add --json-file <scratchpad>/<name>.json
```

Report back what it prints (added/updated, score, id, duplicate status) for each.

## 6. Update the journal

For every source checked this run (regardless of whether any posting from it was
approved), update its entry: `last_checked` = today, `last_found_count` = number of
candidates found there this run, and `notes` if something noteworthy came up (page
moved, nothing found in a long time, worth dropping, etc.). Write the updated
`discovery_journal.json` back.

## 7. Propose new sources (optional)

Up to `discovery.max_new_sources_per_run` per run (default 2 — skip this step
entirely if it's 0). Use WebSearch to find plausible new sources — other small
BC landscape-architecture/urban-design studios, niche design boards — not already
in the journal or in `scrape.py`'s `SOURCES` registry. Present each with a
one-line reason to the user. On approval, append to the journal:
`cadence_days: discovery.default_cadence_days`, `last_checked: today`,
`last_found_count: 0`.

## 8. Summarize

End with: sources checked, postings found/approved/added (with ids and scores),
and any new sources added to the journal.
