# Method

This document explains how every number in the tracker is produced, so results can be checked, challenged and reproduced.

## 1. Collection and cleaning

- **Relevance.** An item is kept if it contains the project name exactly as written, capitals included; or the project's words in another form together with a place or topic word; or a place together with a topic word. Exclude terms block the first two. Items before the project's `start_date` are left out. All of this comes from `project.json` and is described in [CONFIG.md](CONFIG.md). Imported files skip this check: you chose them.
- **Filters.** If the user has switched any on, collected items can be hidden for being in another language, carrying too many hashtags, nearly copying another post from the same week, being too short, containing a blocked phrase, or coming from a source that is switched off. Hidden items take no part in any figure. Imported files are never filtered. The dashboard states how many items each filter hid.
- **Duplicates.** Items are matched by URL, and by a normalised title or opening text, so the same story from GDELT and Google News is kept once. GDELT is collected first, so the copy with full article text wins.
- **Full text.** For GDELT links the article body is fetched and extracted with trafilatura, and used only if it is substantial and itself relevant. Google News links can't be opened, so those items are headline only.

## 2. Sentences

Text is split into sentences, then split again at contrast words: *but, however, although, though, yet, whereas*, and *while* after a comma. This keeps opposing views apart. Up to 80 sentences per item are scored.

## 3. Sentence scores

Each sentence is scored by VADER, a rule-based model for informal English, which returns a compound score from -1 to +1. VADER's word list is extended with a built-in list plus the `lexicon` in `project.json`:

- words with tone in a planning dispute that VADER does not know (*rushed, overruns, blindsided, privatizing, boondoggle*);
- words VADER treats as positive but which in planning coverage name the subject (*safety, security, energy, profits*). These are removed from VADER's list, because a zero entry would still pick up a score from capitals or intensifiers.

Two adjustments are made before scoring: "like" used as a comparison ("projects like this one") is replaced with "such as", and a few attention-grabbing emoji that VADER reads literally (the collision and fire symbols, for example) are dropped.

The project's own name, place names and topic words are also set to 0, because they say what is being discussed, not how people feel about it. Without this, a project with "Prosperity", "Hope" or "Energy" in its name would score as positive every time it was mentioned. An entry in your own `lexicon` overrides this. (The transformer scorer has no word list, so this does not apply to it.)

Optionally, `SCORER=transformer` uses `cardiffnlp/twitter-roberta-base-sentiment-latest`, scoring P(positive) minus P(negative). Don't mix scorers in one database; the report warns if you do.

## 4. Word effects (the evidence)

For each sentence, each word is removed in turn and the sentence scored again:

```
effect(word) = score(sentence) - score(sentence without word)
```

Words with an effect of at least 0.02 are stored, up to 8 per sentence. Because this measures the model's actual response, it captures VADER's rules (negation, intensifiers such as "very", capitals, "but") as well as the word list.

The effects do **not** add up to the sentence score. When several negative words share a sentence, removing one still leaves the others, so each looks smaller than its part in the whole. Read the effects as a ranking of what mattered, not a breakdown that sums.

The dictionary value shown beside each word is VADER's base valence (-4 to +4), before rules apply.

## 5. Themes

A sentence belongs to a theme if it matches one of that theme's patterns (built-in themes, adjusted by `project.json`). Patterns match at the start of a word and ignore case, so `job` matches "jobs" but `pace` does not match "space". Some are short phrases, such as "not told" or "electric bills". Words that are common in neutral reporting ("announced", "told the paper", "meeting") are deliberately left out. A sentence can belong to several themes. An item's theme score is the mean of its sentences in that theme.

## 6. Item score

An item's overall score is the mean of all its sentence scores. Neutral sentences count, so a long factual article sits near zero. That is deliberate: VADER scoring a whole long text at once tends to push it towards -1 or +1, which overstates the tone.

## 7. Weekly figures and weights

Items are grouped by ISO week of publication. For a week and theme:

```
figure = sum(item_score x source_weight) / sum(source_weight)
share(item) = source_weight / sum(source_weight)
```

Each item counts once, whatever its length. Default weights:

| Source | Weight | Reason |
|---|---|---|
| minutes, hearing, survey | 2.0 | Closest record of what residents said |
| facebook | 1.5 | Mostly local, but self-selected and copied by hand |
| bluesky, mastodon, reddit | 1.0 | Public posts; authors may not be local |
| news | 1.0 | Headline only |
| gdelt | 0.75 | Overlaps Google News |
| anything else | 1.0 | Default |

These are judgement calls. They can be overridden under `source_weights` in `project.json`, are shown in the dashboard, and are applied each time the dashboard is built, so changing one needs no new collection.

**Worked example (fictional data).** A week has 4 items with total weight 4.75. A GDELT article scoring -0.15 at weight 0.75 has share 0.75 / 4.75 = 15.8%, and moves the weekly figure by 15.8% x -0.15 = -0.02. The dashboard prints this for every item.

The dashboard also reports the last 4 weeks (against the 4 before) and all time, calculated the same way over the longer period. Weeks with no items appear on the trend chart as gaps, so quiet periods stay visible.

News headlines from Google News end with the outlet's name. The outlet is stored and shown, but removed from the text before scoring.

## 8. Uncertainty

For each week and theme with at least 3 items, the items are resampled with replacement 1,000 times (fixed seed, so the result can be reproduced) and the weighted mean recalculated each time. The 5th and 95th percentiles give the 90% range.

When comparing two weeks, overlapping ranges are labelled *within noise* and separated ranges *likely shift*. This is a rough screen, not a formal significance test. With fewer than 3 items no range is given and the figure is marked *thin*.

## 9. Validation

`sample N --export FILE` writes random items to a CSV. After you label them positive, negative or neutral, `validate FILE` compares your labels with the model's current labels for the same items. It reports the agreement rate, the rate of opposite calls (positive against negative, the errors that matter most), a confusion table and each disagreement. Because it uses current scores, you can change settings, `rescore`, and validate again against the same labels. With fewer than 30 labels, treat the percentages as a rough guide.

`validate` also prints what you would score by calling every item neutral. Collections of news coverage are mostly neutral, so a high agreement rate means little unless it beats that figure.

One small check has been run: 37 collected items about a real project (news headlines and social posts, 31 of them neutral), labelled by an AI assistant that had already seen some of the scores. The default scorer agreed on 33 (89%), against 31 (84%) for neutral-only, with no opposite calls. It caught 4 of the 6 items that carried a view. That is a sanity check, not evidence of accuracy on resident comments, which the tool has not been validated on. Run `validate` on your own data.

## 10. Known weaknesses

- VADER misreads some negation (*"should not carry that risk"* scores positive) and sarcasm.
- Pattern-based themes over-tag and under-tag.
- Who posts online isn't representative of who lives near the project.
- GDELT and Google News coverage of small local outlets is incomplete.
- Weights are subjective. Report the weights you used with any result.
