# Drafting your settings with an AI assistant

`python sentiment_tracker.py init` covers common project types. For anything unusual, an AI assistant can draft a fuller `project.json`. Paste the prompt below into the assistant, fill in the first four lines, then save its answer as `project.json` in this folder.

Always read what comes back. Check the place names, and remove any theme word that is common in neutral reporting.

```text
I'm setting up a sentiment tracker for public discussion of a local project.
Write a project.json file for it.

Project name:
Area (county, town or district):
What it is, in a sentence:
Anything else that shares its name:

Rules for the file:
- Valid JSON only, no comments. Use exactly these keys:
  "project_name", "area_name", "start_date" (YYYY-MM, when public discussion began),
  "relevance": { "project_terms", "exclude_terms", "place_terms", "topic_terms" },
  "theme_additions", "lexicon"
- All relevance terms in lower case.
- project_terms: the ways the press names the project.
- exclude_terms: phrases meaning something else with the same name.
- place_terms: the area, the site, the nearest towns. Real places only.
- topic_terms: 3 to 6 words that say what kind of project it is.
- theme_additions: for each of these themes, up to 8 words or short phrases
  specific to this kind of project: "jobs_economy", "safety",
  "transparency_trust", "cost_finance", "environment", "traffic_amenity".
  Words match from the start of a word, so use stems like "radioactiv".
  Leave out words common in neutral news reports, such as "announce",
  "plan", "meeting" or "company". You may add one or two new themes if the
  project needs them.
- lexicon: up to 10 words with a clearly positive or negative meaning in
  this debate that a general sentiment model might miss, scored from -4 to +4.

Reply with the JSON file only.
```

Then see what it would collect, before storing anything:

```bash
python sentiment_tracker.py collect --preview
```

## Tightening settings with an AI assistant

If the preview keeps unrelated items, paste this prompt, your `project.json` and the preview output into the assistant:

```text
Below are my project.json and the output of a preview run of a sentiment
tracker. Items under WOULD KEEP that are not about my project are mistakes,
and so are real items under WOULD REJECT.

Suggest the smallest changes to project.json that fix them, using only:
- "start_date" (YYYY-MM)
- relevance.exclude_terms (phrases meaning something else)
- relevance.strict (true if the project name appears in ordinary sentences)
- relevance.place_terms and relevance.topic_terms (narrower or extra words)
- filters: "languages" (e.g. ["en"]), "max_hashtags" (a number), "collapse_reposts"
  (true/false), "min_words" (a number), "block_phrases" (a list), "skip_sources" (a list)

The rules are: an item is kept if it contains the project name exactly as
written; or the project's words plus a place or topic word; or a place word
plus a topic word. Explain each change in one line, then give the full
corrected project.json.
```

After changing settings, apply them to anything already collected:

```bash
python sentiment_tracker.py prune          # lists items that no longer fit
python sentiment_tracker.py rescore
```

If the file has a mistake, the tool says which setting is wrong.
