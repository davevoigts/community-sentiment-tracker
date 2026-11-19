# Changelog

## 1.3.2 (2026-11-19)
- `init` with `--project FILE` now prints next-step commands that include that file.

## 1.3.1 (2026-11-19)
- GDELT titles with spaced-out punctuation are tidied, and now match the same story from Google News.
- The dashboard's note box is blue when it only carries information, red when a setting needs attention.

## 1.3.0 (2026-11-19)
Fixes from inspecting a dashboard built on real collected data.
- Scoring: "energy", "profits" and similar subject words no longer count as praise; "like" in comparisons ("projects like this") is neutral; criticism words such as "privatizing" and "boondoggle" added; attention emoji are ignored.
- Scoring: neutral words are removed from the scorer's list instead of set to zero, which stops capitals and intensifiers giving them a score.
- Themes: a river or water body named in a site description no longer counts as an environment comment.
- Filters: `collapse_reposts` also hides social posts that only repeat a news headline. Language is filled in for items stored by older versions.
- New note when nothing has been imported: collected coverage is not local opinion.
- `validate` reports the neutral-only baseline alongside the agreement rate.
- GDELT tone chart labels no longer overlap.

## 1.2.1 (2026-11-19)
- When GDELT refuses requests, its tone data is skipped at once instead of waiting through a second round of retries, and the message says plainly that nothing is wrong with your settings.

## 1.2.0 (2026-11-19)
User-controlled filters, prompted by bot reposts and non-English posts in a live run.
- `filters` section in `project.json`: `languages`, `max_hashtags`, `collapse_reposts`, `min_words`, `block_phrases`, `skip_sources`.
- Filters hide without deleting, never touch imported files, and are off unless set. `init` offers a recommended set.
- `collect --preview`, `check`, `prune` and the dashboard all report what filters are hiding.
- Language reported by Bluesky and Mastodon is now stored; older databases are upgraded in place.

## 1.1.1 (2026-11-19)
- `prune`, `check` and `collect` refuse to run without a `project.json`, instead of judging stored items against example settings.
- `prune` warns when every item fails, since that points to wrong settings.

## 1.1.0 (2026-11-19)
Safeguards so loose settings are caught early.
- `collect --preview`: shows what would be kept (and why) and rejected, without storing anything.
- "Check your settings" warnings after every `collect`, in the dashboard, and from `check`.
- Every item records why it was kept; the dashboard can filter by that reason.
- Setup guide section and AI prompt for tightening settings.
- Scoring: a project's own name, place and topic words are now neutral, so a name like "Prosperity" or a topic like "energy" no longer counts as praise. Run `rescore` to apply this to stored items.

## 1.0.1 (2026-11-19)
Found on the first live run: months-old, unrelated posts were being kept because they contained the project's words.
- Relevance: the name must appear exactly as written, or with a place or topic word. `strict` option for names made of everyday words.
- `start_date` setting (and a setup question) to ignore anything from before the project existed.
- `prune` command to list and remove stored items that no longer fit.

## 1.0.0 (2026-11-19)
First public version.
- Question-and-answer setup (`init`) with project types.
- Collection from GDELT, Google News, Mastodon and Bluesky, plus file imports.
- Sentence-level scoring by theme, with word-level evidence.
- Visible source weights, 90% ranges, and week, 4-week and all-time views.
- Single-file HTML dashboard, CSV reports and a weekly summary.
- `validate` and `rescore` for checking and tuning the scoring.

## 0.6.0 (2026-11-19)
- `init` with no arguments asks six questions and writes `project.json`.
- Project types (nuclear, wind, solar, road, housing, data centre, pipeline) add suitable search and theme words.
- `--type` and `--exclude` options for the one-line form of `init`.
- Step-by-step setup guide and an optional AI prompt for drafting settings.

## 0.5.0 (2026-11-19)
- Fully generic: all project details move to `project.json`. No code editing needed.
- `init` command writes a starter settings file from a project name, area and topic words.
- `--project FILE` and an optional `"database"` setting, for more than one project.
- Script renamed `sentiment_tracker.py`.
- New built-in theme: traffic and amenity (traffic, noise, property values, landscape).
- Examples and tests use a fictional project.

## 0.4.0 (2026-11-19)
- Themes match at word starts, with tighter patterns. Fixes false tags such as "announced" and "told" counting as transparency, "space" matching "pace", and "Bill" counting as cost.
- "Transparency" theme renamed "Transparency & Trust".
- `rescore` command: apply changed themes, word scores or scorer to stored items.
- `sample --export` and `validate`: measure the model against your own labels.
- Dashboard: period switch (latest week, last 4 weeks, all time), one-line takeaway, thin figures greyed out, continuous weeks on the trend chart, configurable project and area names.
- News outlet names are stored and shown but no longer scored.
- One automatic retry on dropped connections.
- 29 tests.

## 0.3.0 (2026-11-19)
- Dashboard app (`dashboard.html`) with theme cards, trend chart, weights panel and an evidence explorer.
- Word-level evidence: measured effect of each word on each sentence score.
- Source weights, per-item shares and 90% bootstrap ranges on every weekly figure.
- Cross-source deduplication; GDELT full article text; stricter relevance with exclusions.
- Reddit collection removed (API access restricted); import via CSV instead.
- Scoring fixes: "safety" neutralised; sentences split at ", while"; speaker names not scored.
- `.txt` imports ignore lines starting `#`.
- Offline test suite and CI.
- README banner, diagram, contributor guide and issue templates.

## 0.2.0
- Added GDELT tone, Bluesky, Mastodon, file import, weekly summary, hand-check sample, chart.

## 0.1.0
- First version: Google News, GDELT, Reddit; VADER scoring by theme; weekly CSV.
