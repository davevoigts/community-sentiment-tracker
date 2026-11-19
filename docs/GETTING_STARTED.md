# Set up your own project

This walks through pointing the tracker at a project of your own, from nothing to a dashboard. The example is fictional: "Riverton Energy Park" in "Marlow County". Swap in your own names.

## 1. Install

```bash
git clone https://github.com/davevoigts/community-sentiment-tracker.git
cd community-sentiment-tracker
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Describe your project

Run this and answer nine short questions:

```bash
python sentiment_tracker.py init
```

```
1. What is the project called (as the press writes it)?: Riverton Energy Park
2. Which county, town or district does it affect?: Marlow County
3. Any other place names that identify it, such as the site or nearest town?: Riverton, Mill Lane
4. What kind of project is it?
     1. Nuclear power plant
     2. Wind farm
     3. Solar farm
     4. Road, bypass or bridge
     5. Housing or mixed development
     6. Data centre
     7. Pipeline or power line
     8. Something else
   Type a number [8]: 2
5. Words that describe what it is (already included: wind farm, turbine, wind energy). Any to add?:
6. Does anything else share the name "Riverton Energy Park"? ... for example: bank, church: bank
7. When did public discussion start? Earlier items are ignored. (year-month, for example 2026-10): 2026-09
8. Could the words "Riverton Energy Park" turn up in ordinary sentences that have nothing to do with the project? (y/n) [n]:
9. Recommended filters: English only, drop posts with more than 3 hashtags (mostly bots and promotion),
   count near-copies of a post once, drop very short posts.
   Use them? You can change each one later (y/n) [y]:
```

Press Enter to skip any question except the first. The tool then writes `project.json` and tells you what it will search for.

**Question 7 keeps old noise out.** Searches reach back months, so without a start date you can pick up unrelated posts from before the project existed.

**Question 8 is for names made of everyday words.** A name like "Golden Future" or "New Horizon" appears in ordinary sentences. Answer `y` and an item must also mention a place or topic word to count.

**Question 9 switches on the recommended filters.** They hide noise without deleting it, and each one can be changed or switched off in `project.json`. See [Filters](CONFIG.md#filters).

**Question 4 matters most.** Choosing a type adds the search words and theme words that suit it. A nuclear plant gets words such as "reactor", "radioactive" and "meltdown"; a wind farm gets "turbine noise" and "shadow flicker". Pick "Something else" for anything not listed, and add your own words in step 3.

Why places and topics matter: plenty of coverage never uses the project's name. A headline like "Marlow County wind farm plan draws crowd" is kept because it has a place **and** a topic word. A place alone is not enough, or every local story would come in.

Prefer one line? The same thing without questions:

```bash
python sentiment_tracker.py init "Riverton Energy Park" --area "Marlow County" \
    --place Riverton "Mill Lane" --type wind --exclude bank --since 2026-09     # add --no-filters to leave filters off
```

## 3. Check and add your words

This step is optional: the answers above are enough to start. To refine, open `project.json` in any text editor. The parts you are most likely to add to:

```json
{
  "project_name": "Riverton Energy Park",
  "area_name": "Marlow County",
  "relevance": {
    "project_terms": ["riverton energy park"],
    "exclude_terms": ["riverton energy park bank"],
    "place_terms": ["marlow county", "riverton", "mill lane"],
    "topic_terms": ["energy", "power plant"]
  },
  "theme_additions": {
    "safety": ["explosion", "fire risk"],
    "heritage": ["listed building", "historic"]
  },
  "lexicon": { "eyesore": -2.5 }
}
```

- **`exclude_terms`**: if something else shares the name (a bank, a church, a sports team), list the phrase here so it is ignored.
- **`theme_additions`**: words for your kind of project. The built-in themes are general (jobs, safety, trust, cost, environment, traffic). A nuclear project might add `"radioactiv"` and `"meltdown"` to `safety`; a wind farm might add `"turbine noise"` and `"shadow flicker"` to `traffic_amenity`. A name that isn't a built-in theme, like `heritage` above, creates a new theme.
- **`lexicon`**: words with a clear positive or negative meaning in your debate that a general model might miss. Scores run from -4 (very negative) to +4 (very positive).

Words match from the start, so `radioactiv` catches "radioactive" and "radioactivity". Every setting is described in [CONFIG.md](CONFIG.md).

## 4. Preview, then collect

First see what your settings would pick up. This stores nothing:

```bash
python sentiment_tracker.py collect --preview
```

It prints up to three lists: a sample of what it **would keep**, grouped by the reason; what it **would reject** as off-topic; and what your filters **would filter out**. Read them all. If unrelated items are being kept, or real ones rejected, see [Tighten what gets collected](#tighten-what-gets-collected), edit `project.json`, and preview again. Two or three rounds is normal.

When the lists look right:

```bash
python sentiment_tracker.py collect
python sentiment_tracker.py dashboard
```

`collect` prints one line per source: how many items were new, already stored, or off-topic. Run it as often as you like; it only adds what is new.

For Bluesky, create an app password in the Bluesky app and set it before collecting:

```bash
export BSKY_HANDLE=yourname.bsky.social
export BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

## 5. Add local records

The best evidence of local opinion is what people said at meetings and in surveys. Save minutes as a `.txt` file, or comments as a `.csv` with a `text` column (and optionally `date` and `speaker`), then:

```bash
python sentiment_tracker.py import council_minutes.txt --source minutes --date 2026-11-12
python sentiment_tracker.py import survey.csv --source survey --date 2026-11-15
```

These count double by default. See `examples/` for the file formats.

## 6. Check the scoring, then tune

```bash
python sentiment_tracker.py sample 10                      # read ten scored items
python sentiment_tracker.py sample 50 --export labels.csv  # or label fifty yourself
python sentiment_tracker.py validate labels.csv            # and see how often the model agrees
```

If a theme is missing things, or a word is being misread, edit `project.json`, then apply the change to everything already collected:

```bash
python sentiment_tracker.py rescore
python sentiment_tracker.py dashboard
```

## Tighten what gets collected

Searches return anything containing your words, so loose settings let unrelated items in. The tool helps you spot this in three places:

- **`collect --preview`** shows what would be kept and rejected before anything is stored.
- **After every `collect`**, and at the top of the dashboard, a "Check your settings" list appears if something looks wrong: no start date and items going back months, most items kept on the name alone, one source supplying nearly everything, or stored items that no longer fit. `python sentiment_tracker.py check` prints the same list at any time.
- **In the dashboard**, each item says why it was kept, and the "Kept because" filter lets you skim one group at a time. Items kept on the name alone are the ones to check first.

What to change in `project.json`:

| Problem | Fix |
|---|---|
| Items from before the project existed | `"start_date": "2026-09"` |
| Another organisation with the same name | Add its phrase to `exclude_terms`, for example `"riverton energy park bank"` |
| The name is made of everyday words and turns up in ordinary sentences | `"strict": true` under `relevance`, so a place or topic word is also needed |
| A place name that exists in several regions | Use a fuller form in `place_terms`, such as `"marlow county"` instead of `"marlow"` |
| A topic word that is too broad ("energy", "development") | Replace it with something narrower in `topic_terms` ("energy park", "wind farm") |
| Real coverage being rejected | Add the wording it uses to `place_terms` or `topic_terms` |
| Bots posting the same headline with rows of hashtags | `"max_hashtags": 3` under `filters` |
| One press release or article shared many times | `"collapse_reposts": true` under `filters` |
| Posts in other languages | `"languages": ["en"]` under `filters` |
| A recurring spam phrase | Add it to `block_phrases` under `filters` |
| A source that only brings noise | Add it to `skip_sources` under `filters`, for example `["mastodon"]` |

Then clear out what no longer fits. The first command only lists; the second removes:

```bash
python sentiment_tracker.py prune
python sentiment_tracker.py prune --apply
```

Files you imported yourself are never removed.

## Troubleshooting

| You see | What it means |
|---|---|
| `No project.json found` | Run `init` first (step 2), in the same folder. |
| `project.json already exists` | Edit that file, or delete it and run `init` again. |
| Lots of items "off-topic" | Normal. If real coverage is being dropped, add its wording to `place_terms` or `topic_terms`. |
| Unrelated items getting in | Set `start_date`, add the unwanted phrase to `exclude_terms`, or set `"strict": true` under `relevance`. Then run `prune` to list stored items that no longer fit, and `prune --apply` to remove them. |
| The dashboard covers far more weeks than the project has existed | Old, unrelated items. Set `start_date` in `project.json`; they disappear from the dashboard at once. |
| `GDELT busy (429)` | GDELT is limiting requests. The tool retries three times, then moves on. Try again later. |
| `[bluesky] skipped: no login set` | Set the two Bluesky variables above. |
| A theme shows "thin data" | Fewer than 3 items. Use the 4-week or all-time view, or import local records. |

For an unusual project, an AI assistant can draft the word lists for you: see [AI_PROMPT.md](AI_PROMPT.md).

## Keeping it private

`project.json`, your database and your imported files are all ignored by git, so nothing about your project is pushed if you fork this repo.
