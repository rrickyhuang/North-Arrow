# Status

_Last updated: 2026-06-13_

## Current

JobHunter's full pipeline is built and working end-to-end (scrape → parse → commute → enrich → score → digest), verified on live data with HTML email confirmed delivering. The main limitation is coverage: only PIBC + CSLA are live, both narrow (planning/landscape, mostly senior/out-of-metro), so today's shortlist is near-empty — which the tool reports honestly rather than hiding.

## Open items

- [ ] Build firm-direct Vancouver design-studio scraper (across all design fields) — the real fix for the thin-coverage / near-empty shortlist
- [ ] Manual job submission: a way to paste a job found on LinkedIn/Indeed so the tool scores and stores it (input method deferred — decide between paste-URL+description, URL-only fetch, or drop-a-text-file)
- [ ] Cover-letter helper, on request per job (output type deferred — alignment talking points vs full draft vs both)
- [ ] Cross-source deduplication
- [ ] Scheduler for hands-off daily runs (Windows Task Scheduler)
- [ ] Decide whether Canada Line should count for commute (currently Expo/Millennium only)

## History

### 2026-06-13
- Built the whole tool in one session, phased: schema/DB, parsers, free transit-commute estimator, scorer, Claude Haiku enrichment, digest delivery, terminal viewer.
- Interviewed and tuned to Ricky's criteria: commute (from Commercial-Broadway) and design-role fit lead; $60k soft floor; no org-type penalty; flexible across all design fields with a public-realm/spatial core; qualification is a separate display-only axis (reach ceiling = intermediate; registration undecided).
- Confirmed Indeed/LinkedIn are Cloudflare-blocked; investigated and rejected Coroflot, IDIBC, We Work Remotely, Dezeen as broad-design sources for a Vancouver search. PIBC + CSLA are the only viable public boards. Lesson: broad-design coverage must come from firm-direct studios, not public boards.
- Switched email from raw markdown to HTML (multipart) after the plain-text version rendered poorly in Gmail; added a `show.py --html` full-database browser report; clarified confusing terminal labels (commute/salary/location).
- Polished the HTML report: client-side search + source/role/qualification filters with a show-disqualified toggle (disqualified hidden by default); out-of-metro jobs now show their real city instead of "Other"; fixed the disqualified pill wrapping mid-title.
- Set up project docs (ProjectContext.md + Status.md). Repo prepared for public release: secrets and personal config gitignored with `.example` templates, MIT license, README.
- Pushed to GitHub (github.com/rrickyhuang/JobHunter).
