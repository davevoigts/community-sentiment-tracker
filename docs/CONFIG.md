# Project settings (`project.json`)

One file describes your project. Create it by running `python sentiment_tracker.py init` and answering the questions (see [GETTING_STARTED.md](GETTING_STARTED.md)), or copy [`examples/project.example.json`](../examples/project.example.json) (a fictional project) and edit it. Only `project_name` is required; everything else has a default.

## Project types

`init` offers these types. Each adds topic words for searching and extra theme words. You can add more of your own afterwards.

| Type | Topic words added | Theme words added (examples) |
|---|---|---|
| `nuclear` | nuclear, reactor, power plant | meltdown, radioactive, nuclear waste, spent fuel |
| `wind` | wind farm, turbine, wind energy | turbine noise, shadow flicker, birds, bats |
| `solar` | solar farm, solar | glare, agricultural land, biodiversity |
| `road` | bypass, road scheme, highway, bridge | rat run, lorries, air quality, woodland |
| `housing` | housing, homes, development | school places, green belt, affordable housing |
| `datacentre` | data center, data centre | water use, cooling, power demand, generators |
| `pipeline` | pipeline, transmission line, pylon | leak, spill, pylons, compulsory purchase |
| `other` | none | none |

## Settings


| Setting | What it does | Default |
|---|---|---|
| `project_name` | Name shown in the dashboard; also the main search term. | Required |
| `area_name` | Area named in the dashboard's caveat. | "the area" |
| `search.news` | Google News searches. Put exact phrases in quotes. | The project name, plus each place with the first topic word |
| `search.gdelt` | GDELT searches. | As above, one place only |
| `search.social` | Bluesky searches. | As above |
| `search.mastodon_tags` | Mastodon hashtags, without the `#`. | The project name with spaces removed |
| `relevance.project_terms` | Phrases that identify the project. An item containing one is kept. | The project name |
| `relevance.exclude_terms` | Phrases that mean a different thing with the same name, such as a bank. | None |
| `relevance.place_terms` | Place names. | The area name |
| `relevance.topic_terms` | Topic words. An item with a place **and** a topic word is kept. | None |
| `relevance.strict` | `true` if the name is made of everyday words: the name alone is then never enough. | `false` |
| `filters` | Optional rules that hide spam, bots, reposts and other noise. See [Filters](#filters). | None, unless you accept the recommended set in `init` |
| `start_date` | Ignore anything published before this (`2026-10` or `2026-10-01`). | None |
| `project_type` | Recorded by `init`; for your reference only. | `other` |
| `theme_additions` | Patterns to add to a theme. A new name creates a new theme. | None |
| `themes` | Replace a theme's patterns entirely, or set it to `null` to remove it. | Built-in themes |
| `lexicon` | Word scores from -4 to +4, added to the sentiment model. | Built-in list |
| `source_weights` | How much one item from each source counts. | See the README |
| `database` | Database file name. | `tracker.db` |

All matching ignores case.

## Relevance

An item is kept when any of these is true:

1. It contains the project name exactly as you wrote it, capitals included ("Riverton Energy Park"), and no `exclude_terms` phrase. Skipped when `strict` is `true`.
2. It contains a `project_terms` phrase in any other form ("riverton energy park") **and** a place or topic word, and no `exclude_terms` phrase.
3. It contains a `place_terms` phrase **and** a `topic_terms` word.

Rule 2 stops ordinary sentences that happen to use the same words from counting. Rule 3 catches coverage that never uses the project's name ("the Marlow County energy plan"). Items published before `start_date` are always left out. Files you import skip all of these checks.

After tightening these settings, `python sentiment_tracker.py prune` lists stored items that no longer fit, and `prune --apply` removes them. Imported files are never removed.

## Filters

Filters hide collected items that match the project but aren't useful: promotional bots, reposts, other languages. **You choose which to switch on.** Nothing is filtered unless `project.json` has a `filters` section. `init` offers a recommended set, shown here:

```json
"filters": {
  "languages": ["en"],
  "max_hashtags": 3,
  "collapse_reposts": true,
  "min_words": 4,
  "block_phrases": [],
  "skip_sources": []
}
```

| Filter | What it hides | To switch off |
|---|---|---|
| `languages` | Posts the source labels as another language. The scorer only reads English, so other languages score as meaningless neutral. Where a source gives no label, posts mostly in a non-Latin script are hidden. | `[]` |
| `max_hashtags` | Posts with more hashtags than this. Rows of hashtags are typical of bots and promotion. | `null` |
| `collapse_reposts` | Social posts that share most of their words with another post from the same week, or that only repeat a news headline with a few words added. A press release shared twenty times counts once; a shared headline with a real comment attached is kept. | `false` |
| `min_words` | Items shorter than this many words. | `0` |
| `block_phrases` | Items containing any phrase you list, for example `"sponsored"` or a bot's sign-off. | `[]` |
| `skip_sources` | Whole sources, for example `["mastodon"]`. They are not fetched at all. | `[]` |

How filters behave:

- **They hide, they don't delete.** Hidden items stay in the database. Change or remove a filter and they return the next time you build the dashboard, with no new collection. `prune --apply` is what deletes.
- **Files you import are never filtered.** You chose those yourself.
- **You can see what they do.** `collect --preview` lists a sample under WOULD FILTER OUT with the reason. `check` and the dashboard header say how many items each filter is hiding. `prune` lists every hidden item.
- **They are blunt.** A real resident who uses five hashtags is hidden too. Skim the preview list when you first switch a filter on.

## Themes

Built-in themes: `jobs_economy`, `safety`, `transparency_trust`, `cost_finance`, `environment`, `traffic_amenity`.

Patterns match at the **start of a word**, so `job` matches "jobs" and "jobless", and `pace` does not match "space". Add `\\b` to end a word exactly: `costs?\\b` matches "cost" and "costs" but not "costume". Patterns are regular expressions, so short phrases work too: `"property values?"`.

Avoid words that are common in neutral reporting, such as "announced" or "meeting". They tag sentences that carry no view.

```json
"theme_additions": {
  "safety": ["explosion", "fire risk"],
  "heritage": ["listed building", "historic"]
},
"themes": { "traffic_amenity": null }
```

After changing themes or the lexicon, run `python sentiment_tracker.py rescore`.

## Lexicon

Add words the model does not know, or override ones it gets wrong for your topic:

```json
"lexicon": { "eyesore": -2.5, "landmark": 0 }
```

Your project's own name, place names and topic words are treated as neutral automatically, so you don't need to list them. Use `sample` and `validate` to find other words worth adding.

## Keeping settings private

`.gitignore` excludes `project.json`, so your project's name and search terms stay out of any fork you push. Bluesky credentials go in environment variables (see `run.sh.example`), never in `project.json`.
