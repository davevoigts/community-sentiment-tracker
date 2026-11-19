#!/usr/bin/env python3
"""
Community sentiment tracker.

Tracks what is being said in public about a project (a power plant, a road, a
wind farm, a data centre...), scores it by theme, and shows the evidence behind
every number. Project details live in project.json; see docs/CONFIG.md.

Automatic sources: Google News RSS, GDELT (articles + GDELT's own tone),
Mastodon hashtags, Bluesky (needs a free app password).
Manual sources:    any .txt or .csv you import (meeting minutes, hearing
                   transcripts, survey answers, comments copied by hand).

Commands
  init                         answer a few questions to set up your project
  init NAME [--area A] [--place P ...] [--type T] [--topic W ...] [--exclude W ...]
                               the same, without the questions
  collect --preview            store nothing; show what your settings would keep
                               and reject, so you can tighten them first
  collect [--headlines-only]   fetch, score and store new items
  check                        warn about settings that look too loose
  import FILE --source NAME --date YYYY-MM-DD
                               (.txt: one item per paragraph; lines starting # ignored.
                                .csv: columns text, and optionally date, speaker, url)
  dashboard [--no-open]        build dashboard.html (the app) and open it
  report                       console table + weekly_report.csv + evidence.csv
  summary                      this week vs last week -> weekly_summary.md
  sample [N] [--theme T]       N random scored items, to check by hand
  sample N --export FILE       write N items to a CSV for you to label
  validate FILE                compare your labels with the model's
  rescore                      re-score stored items after changing themes,
                               word scores or SCORER
  prune [--apply]              list (and with --apply, remove) collected items that
                               your current settings would not keep
  demo                         load fictional sample text, to try it offline

Options
  --project FILE               settings file (default project.json, or $TRACKER_PROJECT)
  BSKY_HANDLE / BSKY_APP_PASSWORD   environment variables for Bluesky
  SCORER=transformer           stronger model (pip install -r requirements-optional.txt)

How scores are made (all of it is shown in the dashboard):
  1. Each item is split into sentences, and sentences are split at "but",
     "however" and similar words.
  2. Each sentence gets a score from -1 to +1.
  3. Word evidence: each word is removed in turn and the sentence re-scored.
     The change is that word's measured effect on the score.
  4. A sentence counts toward a theme if it matches one of that theme's patterns.
     An item's theme score is the mean of its theme sentences.
  5. Period figures are weighted means over items, using the source weights.
     Each item counts once, whatever its length. A 90% bootstrap interval
     shows how much the figure could move with a different sample.

This measures online and news discourse, not the opinion of everyone in the
area. Vocal people and outsiders are over-represented.
"""
import argparse
import csv
import hashlib
import html as htmllib
import json
import os
import random
import re
import sqlite3
import sys
import time
import warnings
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

warnings.filterwarnings("ignore", message=".*OpenSSL.*")
import requests  # noqa: E402

# ======================================================================= config
# Everything specific to a project lives in project.json (see docs/CONFIG.md).
# The values below are the defaults, used for anything project.json leaves out.
VERSION = "1.3.2"

DEFAULT_SOURCE_WEIGHTS = {
    # local records are closest to what residents actually said;
    # GDELT overlaps Google News and is sometimes headline-only.
    "minutes": 2.0, "hearing": 2.0, "survey": 2.0, "facebook": 1.5,
    "reddit": 1.0, "bluesky": 1.0, "mastodon": 1.0,
    "news": 1.0, "gdelt": 0.75,
}
DEFAULT_WEIGHT = 1.0

# Themes. Each entry is matched at the START of a word, ignoring case, so "job"
# matches "jobs" but "pace" does not match "space". Entries are regular
# expressions, so "electric bills?" or "not (?:been )?told" work too.
DEFAULT_THEMES = {
    "jobs_economy": ["jobs?\\b", "employ", "hiring", "hire[sd]?\\b", "workforce", "wages?\\b",
                     "paychecks?", "careers?\\b", "apprentice", "econom", "tax (?:revenue|base)",
                     "revenue", "storefront", "downtown", "business(?:es)?\\b", "opportunit",
                     "investment", "(?:kids|children|grandchildren|young people)\\b.{0,40}\\bstay"],
    "safety": ["safe", "unsafe", "accident", "danger", "hazard", "disaster", "evacuat",
               "emergenc", "health\\b"],
    "transparency_trust": [
        "(?:not|never|nobody|no one|wasn't|weren't|hasn't|haven't)\\s+(?:\\w+\\s+){0,2}"
        "(?:told|notified|consulted|informed|asked|explained)",
        "secre", "transparen", "rush", "too fast", "public (?:input|comment|hearing|meeting)",
        "town hall", "hearing\\b", "consult", "disclos", "behind closed doors", "in the dark",
        "blindsided", "answers?\\b", "unanswered", "dodg", "trust", "honest\\b", "honesty"],
    "cost_finance": ["costs?\\b", "costly", "ratepayer", "taxpayer", "(?:electric|power|utility) bills?",
                     "bills\\b", "rates\\b", "financ", "subsid", "overrun", "over budget", "budget",
                     "bankrupt", "billions?\\b", "afford", "prices?\\b", "who pays", "debt",
                     "loans?\\b"],
    "environment": ["water (?:quality|supply|use|table)", "drinking water", "groundwater",
                    "wildlife", "environment", "emission", "carbon",
                    "clean energy", "climate", "habitat", "flood", "pollut", "ecolog", "fish",
                    "waste\\b"],
    "traffic_amenity": ["traffic", "congestion", "noise", "noisy", "property values?", "house prices?",
                     "home values?", "housing", "rents?\\b", "roads?\\b", "landscape", "skyline",
                     "eyesore", "farmland", "way of life"],
}

# Extra VADER word scores (-4 .. +4): planning-dispute words VADER does not know,
# and "safety"/"security" set to 0 because they name a topic rather than praise it.
DEFAULT_LEXICON = {"rushed": -1.5, "overrun": -1.8, "overruns": -1.8, "secretive": -1.8,
                   "unanswered": -1.2, "blindsided": -2.0, "railroaded": -2.2,
                   "bankrupt": -2.0, "bankruptcy": -2.0, "eyesore": -2.0,
                   "revitalize": 1.8, "revitalise": 1.8, "boost": 1.5,
                   "safety": 0.0, "security": 0.0, "promises": 0.0,
                   # words that describe the subject in planning coverage, not approval
                   "energy": 0.0, "profit": 0.0, "profits": 0.0, "share": 0.0, "shares": 0.0,
                   "united": 0.0, "interest": 0.0, "interests": 0.0,
                   # words of criticism VADER does not know
                   "privatizing": -1.5, "privatising": -1.5, "privatize": -1.2, "privatise": -1.2,
                   "socializing": -1.0, "socialising": -1.0, "boondoggle": -2.5, "bailout": -2.0,
                   "handout": -1.5, "giveaway": -1.5, "unproven": -1.5, "overpriced": -2.0}

EXAMPLE_PROJECT = {"project_name": "Example Project", "area_name": "the area"}

USER_AGENT = "community-sentiment-tracker/" + VERSION + " (research)"
TIMEOUT = 20
THIN = 3             # fewer items than this in a period/theme = treat as noise
NEUTRAL_BAND = 0.05  # |score| below this = neutral
MAX_SENTENCES = 80   # per item, to keep long articles in proportion
BOOTSTRAP = 1000

# Filled in by configure()
PROJECT_NAME = AREA_NAME = DB_PATH = ""
NEWS_QUERIES = GDELT_QUERIES = SOCIAL_QUERIES = MASTODON_TAGS = []
PROJECT_TERMS = EXCLUDE_TERMS = PLACE_TERMS = TOPIC_TERMS = []
THEMES, THEME_RE, SOURCE_WEIGHTS, DOMAIN_LEXICON = {}, {}, {}, {}
STRICT, START_DATE = False, ""
FILTERS = {}
# What `init` writes when you accept the recommended filters. Nothing is filtered
# unless project.json has a "filters" section.
RECOMMENDED_FILTERS = {"languages": ["en"], "max_hashtags": 3, "collapse_reposts": True,
                       "min_words": 4, "block_phrases": [], "skip_sources": []}
SOCIAL_SOURCES = ("bluesky", "mastodon", "reddit", "facebook")
COLLECTED_SOURCES = ("gdelt", "news", "mastodon", "bluesky")
CONFIGURED = False


def load_config(path=None):
    """Read project.json (or the file given). Returns None if there isn't one."""
    path = path or os.environ.get("TRACKER_PROJECT") or "project.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except ValueError as e:
        raise SystemExit(f"{path} is not valid JSON: {e}")
    if not str(cfg.get("project_name", "")).strip():
        raise SystemExit(f"{path} needs a \"project_name\".")
    cfg["_path"] = path
    return cfg


def configure(cfg=None):
    """Apply a project's settings. With no settings, use a neutral example project."""
    global PROJECT_NAME, AREA_NAME, DB_PATH, NEWS_QUERIES, GDELT_QUERIES, SOCIAL_QUERIES
    global MASTODON_TAGS, PROJECT_TERMS, EXCLUDE_TERMS, PLACE_TERMS, TOPIC_TERMS
    global THEMES, THEME_RE, SOURCE_WEIGHTS, DOMAIN_LEXICON, CONFIGURED, _scorer
    global STRICT, START_DATE, FILTERS
    CONFIGURED = cfg is not None
    cfg = cfg or EXAMPLE_PROJECT
    low = lambda xs: [str(x).lower() for x in xs if str(x).strip()]
    PROJECT_NAME = cfg["project_name"].strip()
    AREA_NAME = str(cfg.get("area_name") or "the area").strip()
    rel = cfg.get("relevance", {})
    PROJECT_TERMS = low(rel.get("project_terms") or [PROJECT_NAME])
    EXCLUDE_TERMS = low(rel.get("exclude_terms", []))
    PLACE_TERMS = low(rel.get("place_terms") or ([cfg["area_name"]] if cfg.get("area_name") else []))
    TOPIC_TERMS = low(rel.get("topic_terms", []))
    STRICT = bool(rel.get("strict", False))
    f = cfg.get("filters") or {}
    unknown = set(f) - set(RECOMMENDED_FILTERS)
    if unknown:
        raise SystemExit(f"Unknown filter(s) in project.json: {', '.join(sorted(unknown))}. "
                         f"Available: {', '.join(RECOMMENDED_FILTERS)}.")
    FILTERS = {"languages": [str(x).lower()[:2] for x in f.get("languages", [])],
               "max_hashtags": f.get("max_hashtags"), "collapse_reposts": bool(f.get("collapse_reposts")),
               "min_words": int(f.get("min_words") or 0),
               "block_phrases": [str(x).lower() for x in f.get("block_phrases", []) if str(x).strip()],
               "skip_sources": [str(x).lower() for x in f.get("skip_sources", [])]}
    START_DATE = str(cfg.get("start_date") or "").strip()
    if START_DATE:
        if re.fullmatch(r"\d{4}-\d\d", START_DATE):
            START_DATE += "-01"
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", START_DATE):
            raise SystemExit('"start_date" must look like 2026-10-01 or 2026-10.')
    topic = TOPIC_TERMS[0] if TOPIC_TERMS else ""
    place_q = [f'"{p}" {topic}'.strip() for p in PLACE_TERMS[:2]] if topic else []
    srch = cfg.get("search", {})
    NEWS_QUERIES = srch.get("news") or [f'"{PROJECT_NAME}"'] + place_q
    GDELT_QUERIES = srch.get("gdelt") or [f'"{PROJECT_NAME}"'] + place_q[:1]
    SOCIAL_QUERIES = srch.get("social") or [f'"{PROJECT_NAME}"'] + \
        [f"{p} {topic}" for p in PLACE_TERMS[:2] if topic]
    MASTODON_TAGS = srch.get("mastodon_tags") or [re.sub(r"\W", "", PROJECT_NAME.title())]
    THEMES = {k: list(v) for k, v in DEFAULT_THEMES.items()}
    for k, v in cfg.get("themes", {}).items():          # replace or remove a theme
        if v is None:
            THEMES.pop(k, None)
        else:
            THEMES[k] = list(v)
    for k, v in cfg.get("theme_additions", {}).items():  # add patterns to a theme
        THEMES.setdefault(k, []).extend(v)
    THEME_RE = {}
    for t, pats in THEMES.items():
        try:
            THEME_RE[t] = re.compile(r"\b(?:" + "|".join(pats) + ")", re.I)
        except re.error as e:
            raise SystemExit(f"Theme '{t}' has a pattern that is not a valid regular expression: {e}")
    SOURCE_WEIGHTS = {**DEFAULT_SOURCE_WEIGHTS, **cfg.get("source_weights", {})}
    # The project's own name, places and topic words say what is being discussed,
    # not how people feel about it, so they are scored as neutral ("Prosperity",
    # "energy" and "safe" all count as positive words otherwise).
    own_words = {w for phrase in [PROJECT_NAME] + PROJECT_TERMS + PLACE_TERMS + TOPIC_TERMS
                 for w in re.findall(r"[a-z']+", phrase.lower())}
    DOMAIN_LEXICON = {**DEFAULT_LEXICON, **{w: 0.0 for w in own_words},
                      **{k.lower(): v for k, v in cfg.get("lexicon", {}).items()}}
    DB_PATH = cfg.get("database") or "tracker.db"
    _scorer = None


# Ready-made word lists by project type, used by `init`.
PROJECT_TYPES = {
    "nuclear": {"label": "Nuclear power plant",
                "topic": ["nuclear", "reactor", "power plant"],
                "theme_additions": {"safety": ["meltdown", "radiat", "radioactiv", "nuclear waste",
                                               "spent fuel", "chernobyl", "fukushima",
                                               "three mile island"],
                                    "cost_finance": ["vogtle", "hinkley"]},
                "lexicon": {"meltdown": -2.5}},
    "wind": {"label": "Wind farm",
             "topic": ["wind farm", "turbine", "wind energy"],
             "theme_additions": {"traffic_amenity": ["turbine noise", "shadow flicker", "blades?\\b"],
                                 "environment": ["birds?\\b", "bats?\\b", "seabed"]}},
    "solar": {"label": "Solar farm",
              "topic": ["solar farm", "solar"],
              "theme_additions": {"traffic_amenity": ["glare", "agricultural land"],
                                  "environment": ["biodiversity", "soil"]}},
    "road": {"label": "Road, bypass or bridge",
             "topic": ["bypass", "road scheme", "highway", "bridge"],
             "theme_additions": {"traffic_amenity": ["rat run", "lorries", "trucks?\\b", "hgvs?\\b"],
                                 "environment": ["air quality", "woodland"]}},
    "housing": {"label": "Housing or mixed development",
                "topic": ["housing", "homes", "development"],
                "theme_additions": {"traffic_amenity": ["school places", "doctors", "surger",
                                                        "green belt", "overdevelop", "infrastructure"],
                                    "cost_finance": ["affordable hom", "affordable housing"]}},
    "datacentre": {"label": "Data centre",
                   "topic": ["data center", "data centre"],
                   "theme_additions": {"environment": ["water use", "cooling", "power demand", "grid"],
                                       "traffic_amenity": ["hum\\b", "generators?\\b"]}},
    "pipeline": {"label": "Pipeline or power line",
                 "topic": ["pipeline", "transmission line", "pylon"],
                 "theme_additions": {"safety": ["leak", "spill", "rupture"],
                                     "traffic_amenity": ["pylons?\\b", "eminent domain", "easement",
                                                         "compulsory purchase"]}},
    "other": {"label": "Something else", "topic": [], "theme_additions": {}},
}


def init_project(name, area="", places=(), topics=(), path="project.json", kind="other",
                 excludes=(), start_date="", strict=False, filters=True):
    """Write a starter project.json."""
    if os.path.exists(path):
        raise SystemExit(f"{path} already exists. Edit it, or delete it and run init again.")
    if kind not in PROJECT_TYPES:
        raise SystemExit(f"Unknown type '{kind}'. Choose from: {', '.join(PROJECT_TYPES)}")
    preset = PROJECT_TYPES[kind]
    topics = list(dict.fromkeys([t.lower() for t in list(topics) + preset["topic"]]))
    configure({"project_name": name, "area_name": area,
               "relevance": {"place_terms": ([area] if area else []) + list(places),
                             "topic_terms": topics}})
    cfg = {
        "project_name": PROJECT_NAME, "area_name": area or "", "project_type": kind,
        "start_date": start_date,
        "search": {"news": NEWS_QUERIES, "gdelt": GDELT_QUERIES, "social": SOCIAL_QUERIES,
                   "mastodon_tags": MASTODON_TAGS},
        "relevance": {"project_terms": PROJECT_TERMS,
                      "exclude_terms": [f"{PROJECT_NAME} {x}".lower() for x in excludes],
                      "place_terms": PLACE_TERMS, "topic_terms": TOPIC_TERMS,
                      "strict": bool(strict)},
        "filters": dict(RECOMMENDED_FILTERS) if filters else {},
        "theme_additions": preset["theme_additions"], "lexicon": preset.get("lexicon", {}),
        "source_weights": {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    configure(cfg)  # fail now, not later, if anything in it is wrong
    print(f"\nWrote {path} for \"{PROJECT_NAME}\".")
    print("  Searching for: " + "; ".join(NEWS_QUERIES))
    if START_DATE:
        print(f"  Ignoring anything published before {START_DATE}.")
    print("  Keeping items that name the project" + (" with a place or topic word" if STRICT else "") +
          (f", or that mention {', '.join(PLACE_TERMS)} together with {', '.join(TOPIC_TERMS)}"
           if PLACE_TERMS and TOPIC_TERMS else "") + ".")
    print("  Filters: " + ("English only; posts with more than 3 hashtags dropped; near-copies "
                           "of a post counted once; very short posts dropped."
                           if filters else "none."))
    added = sum(len(v) for v in preset["theme_additions"].values())
    if added:
        print(f"  Added {added} theme words for: {preset['label'].lower()}.")
    run = "python sentiment_tracker.py" + ("" if path == "project.json" else f" --project {path}")
    print("Next, see what these settings would pick up before storing anything:\n"
          f"  {run} collect --preview\n"
          f"then:\n  {run} collect\n  {run} dashboard\n"
          f"You can edit {path} at any time; docs/CONFIG.md explains every setting.")


def ask(question, default=""):
    try:
        answer = input(f"{question}{' [' + default + ']' if default else ''}: ").strip()
    except EOFError:
        raise SystemExit("\nSetup cancelled.")
    return answer or default


def split_list(text):
    return [x.strip().strip('"') for x in text.split(",") if x.strip()]


def init_wizard(path="project.json"):
    """Ask a few questions and write project.json."""
    if os.path.exists(path):
        raise SystemExit(f"{path} already exists. Edit it, or delete it and run init again.")
    print("This sets up the tracker for your project. Press Enter to skip a question.\n")
    name = ""
    while not name:
        name = ask("1. What is the project called (as the press writes it)?")
    area = ask("2. Which county, town or district does it affect?")
    places = split_list(ask("3. Any other place names that identify it, such as the site or "
                            "nearest town? (separate with commas)"))
    print("4. What kind of project is it?")
    kinds = list(PROJECT_TYPES)
    for n, k in enumerate(kinds, 1):
        print(f"     {n}. {PROJECT_TYPES[k]['label']}")
    pick = ask("   Type a number", str(len(kinds)))
    kind = kinds[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(kinds) else "other"
    preset_topics = PROJECT_TYPES[kind]["topic"]
    hint = f" (already included: {', '.join(preset_topics)})" if preset_topics else \
        ", for example: quarry, stadium, airport"
    topics = split_list(ask(f"5. Words that describe what it is{hint}. Any to add? "
                            "(separate with commas)"))
    excludes = split_list(ask(f"6. Does anything else share the name \"{name}\"? Type what follows "
                              "the name, for example: bank, church (separate with commas)"))
    start = ask("7. When did public discussion start? Earlier items are ignored. "
                "(year-month, for example 2026-10)")
    everyday = ask(f"8. Could the words \"{name}\" turn up in ordinary sentences that have "
                   "nothing to do with the project? (y/n)", "n").lower().startswith("y")
    print("9. Recommended filters: English only, drop posts with more than 3 hashtags (mostly "
          "bots and promotion),\n   count near-copies of a post once, drop very short posts.")
    filters = not ask("   Use them? You can change each one later (y/n)", "y").lower().startswith("n")
    init_project(name, area, places, topics, path, kind, excludes, start, everyday, filters)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def weight_of(source):
    return SOURCE_WEIGHTS.get(source, DEFAULT_WEIGHT)


def label(x):
    return "positive" if x >= NEUTRAL_BAND else "negative" if x <= -NEUTRAL_BAND else "neutral"


REASONS = {"name": "names the project", "name+context": "project words + place or topic",
           "place+topic": "place + topic word", "imported": "imported by you"}


def relevance_reason(text):
    """Why an item is kept, or None if it is not. An item is kept if it:
      "name"          names the project exactly as written, capitals included, or
      "name+context"  uses the project's words in any other form AND has a place or
                      topic word, or
      "place+topic"   has a place word AND a topic word.
    "name" is skipped when "strict" is set, for names made of everyday words.
    Anything containing an exclude term fails the first two."""
    low = text.lower()
    has_place = any(p in low for p in PLACE_TERMS)
    has_topic = any(k in low for k in TOPIC_TERMS)
    if not any(x in low for x in EXCLUDE_TERMS):
        named = PROJECT_NAME in text
        if (named or any(t in low for t in PROJECT_TERMS)) and (has_place or has_topic):
            return "name+context"
        if named and not STRICT:
            return "name"
    return "place+topic" if has_place and has_topic else None


def is_relevant(text):
    return relevance_reason(text) is not None


def after_start(published):
    return not START_DATE or (published or "9999")[:10] >= START_DATE


FILTER_NAMES = {"language": "not in your chosen language", "hashtags": "too many hashtags",
                "short": "too short", "phrase": "contains a blocked phrase",
                "repost": "repeats a headline or another post", "source": "source switched off"}


def guess_not_latin(text):
    """True when most letters are outside the Latin alphabet (used only when a
    source does not say what language a post is in)."""
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) >= 10 and sum(ord(ch) > 0x24F for ch in letters) / len(letters) > 0.5


def static_filter(source, text, lang=""):
    """Reason an item is hidden by the user's filters, or None. Imports are never filtered."""
    if source not in COLLECTED_SOURCES:
        return None
    if source in FILTERS["skip_sources"]:
        return "source"
    langs = FILTERS["languages"]
    if langs:
        if lang and lang.lower()[:2] not in langs:
            return "language"
        if not lang and "en" in langs and guess_not_latin(text):
            return "language"
    mh = FILTERS["max_hashtags"]
    if mh is not None and len(re.findall(r"#\w+", text)) > int(mh):
        return "hashtags"
    low = text.lower()
    if any(p in low for p in FILTERS["block_phrases"]):
        return "phrase"
    if FILTERS["min_words"] and len(re.findall(r"\w+", text)) < FILTERS["min_words"]:
        return "short"
    return None


def word_set(text):
    text = re.sub(r"https?://\S+|#\w+|@\S+", " ", text.lower())
    return set(re.findall(r"[a-z0-9']+", text))


def apply_filters(items):
    """Split items into (shown, hidden). Each hidden item gets item["hidden"] = reason."""
    shown, hidden, seen = [], [], []
    day_of = lambda i: datetime.strptime(i["published"][:10], "%Y-%m-%d")
    headlines = [(day_of(i), word_set(i["title"])) for i in items
                 if i["source"] in ("news", "gdelt") and i["title"]]
    headlines = [(d, w) for d, w in headlines if len(w) >= 5]
    for it in sorted(items, key=lambda i: i["published"]):
        text = " ".join(x["text"] for x in it["sentences"])
        why = static_filter(it["source"], text, it.get("lang", ""))
        if not why and FILTERS["collapse_reposts"] and it["source"] in SOCIAL_SOURCES \
                and it["source"] in COLLECTED_SOURCES:
            ws = word_set(text)
            # a post that is just a news headline, with at most a few words added
            for d, head in headlines:
                if abs((day_of(it) - d).days) <= 7 and len(ws & head) / len(head) >= 0.8 \
                        and len(ws - head) <= 6:
                    why = "repost"
                    break
            if not why and len(ws) >= 5:
                for day, other in seen:
                    if it["published"][:10] <= day and len(ws & other) / len(ws | other) >= 0.6:
                        why = "repost"
                        break
                if not why:
                    d = datetime.strptime(it["published"][:10], "%Y-%m-%d") + timedelta(days=7)
                    seen.append((d.strftime("%Y-%m-%d"), ws))
        if why:
            it["hidden"] = why
            hidden.append(it)
        else:
            shown.append(it)
    return shown, hidden


def filter_summary(hidden):
    counts = {}
    for it in hidden:
        counts[it["hidden"]] = counts.get(it["hidden"], 0) + 1
    return ", ".join(f"{n} {FILTER_NAMES[k]}" for k, n in sorted(counts.items(), key=lambda x: -x[1]))


# ====================================================================== scoring
class Scorer:
    def __init__(self, kind):
        self.kind = kind
        if kind == "transformer":
            from transformers import pipeline
            self.pipe = pipeline("sentiment-analysis",
                                 model="cardiffnlp/twitter-roberta-base-sentiment-latest",
                                 top_k=None, truncation=True)
        else:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            self.vader = SentimentIntensityAnalyzer()
            for word, value in DOMAIN_LEXICON.items():
                if value == 0:
                    # Neutral words are removed, not set to 0: VADER still applies
                    # capitals and intensifiers ("LEWIS", "enormous energy") to a 0 entry.
                    self.vader.lexicon.pop(word, None)
                else:
                    self.vader.lexicon[word] = value

    # "like" is praise in "I like it" but not in "projects like this one". VADER
    # cannot tell them apart, so the comparison form is neutralised first.
    LIKE = re.compile(r"(?<!\bi )(?<!\bwe )(?<!\byou )(?<!\bthey )(?<!\bpeople )(?<!\breally )"
                      r"(?<!\bdo )(?<!\bwould )(?<!\bi'd )(?<!\bwe'd )(?<!\bdon't )"
                      r"(?<!\bresidents )\blike\b", re.I)

    # Attention-grabbing emoji that VADER reads literally ("collision", "fire", "siren").
    HYPE = re.compile("[\U0001F4A5\U0001F525\u26A1\U0001F6A8\U0001F4A3\U0001F4C8\U0001F4C9]")

    def __call__(self, text):
        if self.kind != "transformer":
            text = self.HYPE.sub(" ", self.LIKE.sub("such as", text))
        if self.kind == "transformer":
            probs = {d["label"].lower(): d["score"] for d in self.pipe(text[:2000])[0]}
            return probs.get("positive", 0.0) - probs.get("negative", 0.0)
        return self.vader.polarity_scores(text)["compound"]

    def lexicon_value(self, word):
        if self.kind == "transformer":
            return None
        word = word.lower().strip(".,!?;:\"'()[]")
        return 0.0 if DOMAIN_LEXICON.get(word) == 0 else self.vader.lexicon.get(word)


_scorer = None


def scorer():
    global _scorer
    if _scorer is None:
        _scorer = Scorer(os.environ.get("SCORER", "vader").lower())
    return _scorer


CONTRAST = re.compile(r"[,;]?\s+\b(?:but|however|although|though|yet|whereas)\b\s*|"
                      r"[,;]\s+while\b\s*", re.I)


def split_sentences(text):
    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        out += [c.strip() for c in CONTRAST.split(sent) if len(c.strip()) > 8]
    return out


def themes_of(sentence):
    return [t for t, rx in THEME_RE.items() if rx.search(sentence)]


def word_evidence(sentence, base):
    """Leave-one-out: re-score the sentence with each word removed.
    Returns [(index, word, effect, lexicon value)] for words that moved the score."""
    sc = scorer()
    toks = sentence.split()
    if len(toks) > 60:
        return []
    ev = []
    for i, w in enumerate(toks):
        without = " ".join(toks[:i] + toks[i + 1:])
        d = base - (sc(without) if without else 0.0)
        if abs(d) >= 0.02:
            ev.append((i, w.strip(".,!?;:\"'()[]"), round(d, 3), sc.lexicon_value(w)))
    ev.sort(key=lambda e: -abs(e[2]))
    return ev[:8]


def analyse(text):
    """Score every sentence; return (overall, sentences, theme_scores)."""
    sc = scorer()
    sents = split_sentences(text)[:MAX_SENTENCES] or ([text.strip()] if text.strip() else [])
    rows = []
    for s in sents:
        score = sc(s)
        th = themes_of(s)
        # word evidence is cheap with VADER; with the transformer, only for theme sentences
        ev = word_evidence(s, score) if (sc.kind == "vader" or th) else []
        rows.append({"text": s, "score": round(score, 4), "themes": th, "evidence": ev})
    overall = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    per_theme = {}
    for r in rows:
        for t in r["themes"]:
            per_theme.setdefault(t, []).append(r["score"])
    return overall, rows, {t: (sum(v) / len(v), len(v)) for t, v in per_theme.items()}


# ====================================================================== storage
SCHEMA_VERSION = 3


def db():
    if os.path.exists(DB_PATH):
        con = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in con.execute("PRAGMA table_info(items)")]
        con.close()
        if cols and "speaker" not in cols:
            backup = DB_PATH.replace(".db", "_v2_backup.db")
            os.replace(DB_PATH, backup)
            print(f"Found a tracker.db from an older version; moved it to {backup} "
                  "and started a new one. Run `collect` to refill it.")
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS items(
            id TEXT PRIMARY KEY, source TEXT, url TEXT, title TEXT, text TEXT,
            published TEXT, fetched TEXT, overall REAL, scorer TEXT, norm_key TEXT,
            fulltext INTEGER DEFAULT 0, speaker TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS ix_norm ON items(norm_key);
        CREATE TABLE IF NOT EXISTS sentences(
            item_id TEXT, idx INTEGER, text TEXT, score REAL, themes TEXT, evidence TEXT,
            PRIMARY KEY(item_id, idx));
        CREATE TABLE IF NOT EXISTS theme_scores(
            item_id TEXT, theme TEXT, score REAL, n_sentences INTEGER,
            PRIMARY KEY(item_id, theme));
        CREATE TABLE IF NOT EXISTS gdelt_tone(
            date TEXT, query TEXT, tone REAL, PRIMARY KEY(date, query));
    """)
    if "lang" not in [r[1] for r in con.execute("PRAGMA table_info(items)")]:
        con.execute("ALTER TABLE items ADD COLUMN lang TEXT DEFAULT ''")
    return con


def norm_key(title, text):
    """Key for spotting the same story from two sources. Google News titles end
    with ' - Publisher', which is dropped."""
    base = title or text[:200]
    if " - " in base:
        base = base.rsplit(" - ", 1)[0]
    # letters and digits only, so "6 . 6 Gigawatt" and "6.6 Gigawatt" match
    return re.sub(r"[^a-z0-9]", "", base.lower())[:150]


def already_have(con, url, title, text):
    full = ". ".join(x for x in (title.strip(), (text or "").strip()) if x)
    iid = hashlib.sha1((url or full[:300]).encode("utf-8")).hexdigest()[:16]
    key = norm_key(title, text or "")
    return bool(con.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone() or
                (len(key) > 25 and con.execute("SELECT 1 FROM items WHERE norm_key=?",
                                                (key,)).fetchone()))


def store_scores(con, iid, scored):
    """Score text and (re)write its sentence and theme rows. Returns the overall score."""
    overall, rows, themes = analyse(scored)
    con.execute("DELETE FROM sentences WHERE item_id=?", (iid,))
    con.execute("DELETE FROM theme_scores WHERE item_id=?", (iid,))
    for i, r in enumerate(rows):
        con.execute("INSERT INTO sentences VALUES(?,?,?,?,?,?)",
                    (iid, i, r["text"], r["score"], ",".join(r["themes"]),
                     json.dumps(r["evidence"])))
    for t, (sc, n) in themes.items():
        con.execute("INSERT INTO theme_scores VALUES(?,?,?,?)", (iid, t, sc, n))
    return overall


def scored_text(title, text):
    return ". ".join(x for x in ((title or "").strip(), (text or "").strip()) if x)


def save_item(con, source, url, title, text, published, fulltext=False, speaker="", lang=""):
    """Returns 'new', 'duplicate' or 'empty'. `speaker` (a person or news outlet)
    is stored and shown, but never scored."""
    scored = scored_text(title, text)
    if not scored:
        return "empty"
    if already_have(con, url, title, text):
        return "duplicate"
    iid = hashlib.sha1((url or scored[:300]).encode("utf-8")).hexdigest()[:16]
    con.execute("INSERT INTO items(id, source, url, title, text, published, fetched, overall, "
                "scorer, norm_key, fulltext, speaker, lang) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, source, url, title, text or "", published, now_iso(), 0.0,
                 scorer().kind, norm_key(title, text or ""), int(fulltext), speaker.strip(),
                 (lang or "").lower()[:5]))
    overall = store_scores(con, iid, scored)
    con.execute("UPDATE items SET overall=? WHERE id=?", (overall, iid))
    return "new"


def rescore():
    """Re-score every stored item with the current THEMES, DOMAIN_LEXICON and SCORER."""
    con = db()
    rows = con.execute("SELECT id, title, text FROM items").fetchall()
    for iid, title, text in rows:
        overall = store_scores(con, iid, scored_text(title, text))
        con.execute("UPDATE items SET overall=?, scorer=?, norm_key=? WHERE id=?",
                    (overall, scorer().kind, norm_key(title, text), iid))
    con.commit()
    con.close()
    print(f"Re-scored {len(rows)} items with the current settings ({scorer().kind}).")


# ====================================================================== fetching
def get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", {"User-Agent": USER_AGENT})
    try:
        r = requests.get(url, **kw)
    except (requests.ConnectionError, requests.Timeout):
        time.sleep(3)  # one retry for a dropped connection
        r = requests.get(url, **kw)
    r.raise_for_status()
    return r


def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"<br\s*/?>|</p>", "\n", s)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t]+", " ", s).strip()


def article_text(url):
    if not url or "news.google.com" in url:
        return ""
    try:
        page = get(url).text
    except Exception:
        return ""
    try:
        import trafilatura
        return trafilatura.extract(page) or ""
    except ImportError:
        return " ".join(strip_html(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", page))[:20000]


def fetch_news():
    out = []
    for q in NEWS_QUERIES:
        r = get("https://news.google.com/rss/search?q=" + quote_plus(q) +
                "&hl=en-US&gl=US&ceid=US:en")
        for it in ET.fromstring(r.content).iter("item"):
            try:
                pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).isoformat()
            except Exception:
                pub = now_iso()
            # The RSS description only repeats the headline. Titles end " - Outlet":
            # the outlet is kept for display but not scored.
            title = (it.findtext("title") or "").strip()
            outlet = (it.findtext("source") or "").strip()
            if " - " in title:
                title, tail = title.rsplit(" - ", 1)
                outlet = outlet or tail
            out.append(("news", (it.findtext("link") or "").strip(), title.strip(), "",
                        pub, outlet, "en"))
    return out


def _gdelt(params):
    for wait in (15, 30, 60, None):
        try:
            r = get("https://api.gdeltproject.org/api/v2/doc/doc",
                    params={"format": "json", "timespan": "3m", **params})
            break
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 429 or wait is None:
                raise
            print(f"  GDELT busy (429), waiting {wait}s and retrying...")
            time.sleep(wait)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("GDELT returned non-JSON: " + r.text[:120])


def fetch_gdelt():
    out = []
    for q in GDELT_QUERIES:
        data = _gdelt({"query": q + " sourcelang:english", "mode": "ArtList",
                       "maxrecords": 75, "sort": "DateDesc"})
        for a in data.get("articles", []):
            try:
                pub = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc).isoformat()
            except ValueError:
                pub = now_iso()
            # GDELT spaces out punctuation: "Park , a 6 . 6 Gigawatt" -> "Park, a 6.6 Gigawatt"
            title = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", a.get("title", ""))
            title = re.sub(r"\s+([,.:;!?])", r"\1", title).strip()
            out.append(("gdelt", a.get("url", ""), title, "", pub, "", "en"))
        time.sleep(6)
    return out


def fetch_gdelt_tone(con):
    n = 0
    for q in GDELT_QUERIES:
        data = _gdelt({"query": q + " sourcelang:english", "mode": "TimelineTone"})
        for series in data.get("timeline", []):
            for pt in series.get("data", []):
                try:
                    d = datetime.strptime(pt.get("date", "")[:8], "%Y%m%d").date().isoformat()
                except ValueError:
                    continue
                con.execute("INSERT OR REPLACE INTO gdelt_tone VALUES(?,?,?)",
                            (d, q, float(pt.get("value", 0))))
                n += 1
        time.sleep(6)
    con.commit()
    return n


def fetch_bluesky():
    handle, pw = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    if not (handle and pw):
        raise RuntimeError("no login set. Create an app password in Bluesky settings, then "
                           "set BSKY_HANDLE and BSKY_APP_PASSWORD (see README).")
    r = requests.post("https://bsky.social/xrpc/com.atproto.server.createSession",
                      json={"identifier": handle, "password": pw}, timeout=TIMEOUT)
    if r.status_code in (400, 401):
        raise RuntimeError("login refused. Check BSKY_HANDLE and BSKY_APP_PASSWORD.")
    r.raise_for_status()
    headers = {"User-Agent": USER_AGENT, "Authorization": "Bearer " + r.json()["accessJwt"]}
    out = []
    for q in SOCIAL_QUERIES:
        r = get("https://bsky.social/xrpc/app.bsky.feed.searchPosts",
                params={"q": q, "limit": 100, "sort": "latest"}, headers=headers)
        for p in r.json().get("posts", []):
            rec = p.get("record", {})
            h = p.get("author", {}).get("handle", "")
            rkey = p.get("uri", "").rsplit("/", 1)[-1]
            out.append(("bluesky", f"https://bsky.app/profile/{h}/post/{rkey}", "",
                        rec.get("text", ""), rec.get("createdAt") or now_iso(), "",
                        (rec.get("langs") or [""])[0] or ""))
    return out


def fetch_mastodon():
    inst = os.environ.get("MASTODON_INSTANCE", "mastodon.social")
    out = []
    for tag in MASTODON_TAGS:
        r = get(f"https://{inst}/api/v1/timelines/tag/{tag}", params={"limit": 40})
        for s in r.json():
            out.append(("mastodon", s.get("url") or s.get("uri", ""), "",
                        strip_html(s.get("content", "")), s.get("created_at") or now_iso(), "",
                        s.get("language") or ""))
    return out


# GDELT first: when the same story appears in both, the GDELT copy (which may
# carry full article text) is kept and the headline-only Google News copy is skipped.
SOURCES = {"gdelt": fetch_gdelt, "news": fetch_news, "mastodon": fetch_mastodon,
           "bluesky": fetch_bluesky}


def collect(headlines_only=False, preview=False):
    """Fetch, score and store. With preview=True nothing is stored: it only shows
    what the current settings would keep and reject."""
    con = None if preview else db()
    kept, rejected, filtered = [], [], []
    gdelt_busy = False
    for name, fn in SOURCES.items():
        if name in FILTERS["skip_sources"]:
            print(f"[{name}] switched off in project.json")
            continue
        try:
            rows = fn()
        except Exception as e:
            if name == "gdelt" and "429" in str(e):
                gdelt_busy = True
                print("[gdelt] skipped: GDELT is limiting requests right now. Nothing is wrong "
                      "with your settings; try again in an hour or so.")
            else:
                print(f"[{name}] skipped: {e}")
            continue
        c = {"new": 0, "duplicate": 0, "empty": 0, "off-topic": 0, "full text": 0, "old": 0,
             "filtered": 0}
        for row in rows:
            source, url, title, text, pub = row[:5]
            who = row[5] if len(row) > 5 else ""
            lang = row[6] if len(row) > 6 else ""
            line = f"[{source}] {pub[:10]}  " + " ".join(f"{title} {text}".split())[:110]
            reason = relevance_reason(f"{title} {text}")
            if reason is None:
                c["off-topic"] += 1
                rejected.append(line)
                continue
            if not after_start(pub):
                c["old"] += 1
                continue
            if lang and url and not preview:  # fill in language for items stored before v1.2
                con.execute("UPDATE items SET lang=? WHERE url=? AND (lang IS NULL OR lang='')",
                            (lang.lower()[:5], url))
            why = static_filter(source, f"{title} {text}", lang)
            if why:
                c["filtered"] += 1
                filtered.append(f"{line}   <- {FILTER_NAMES[why]}")
                continue
            if preview:
                c["new"] += 1
                kept.append((reason, line))
                continue
            got_full = False
            if already_have(con, url, title, text):
                c["duplicate"] += 1
                continue
            if source == "gdelt" and not headlines_only:
                body = article_text(url)
                if len(body) > 300 and is_relevant(body):
                    text, got_full = body, True
            res = save_item(con, source, url, title, text, pub, got_full, who, lang)
            c[res] += 1
            c["full text"] += got_full and res == "new"
        if preview:
            print(f"[{name}] fetched {len(rows)}: would keep {c['new']}, reject "
                  f"{c['off-topic']} as off-topic" +
                  (f", {c['old']} as before {START_DATE}" if c["old"] else "") +
                  (f", filter out {c['filtered']}" if c["filtered"] else ""))
            continue
        con.commit()
        extra = f", {c['full text']} with full article text" if name == "gdelt" else ""
        print(f"[{name}] fetched {len(rows)}: {c['new']} new, {c['duplicate']} already "
              f"stored, {c['off-topic']} off-topic{extra}" +
              (f", {c['old']} before {START_DATE}" if c["old"] else "") +
              (f", {c['filtered']} filtered out" if c["filtered"] else ""))
    if preview:
        return show_preview(kept, rejected, filtered)
    if gdelt_busy or "gdelt" in FILTERS["skip_sources"]:
        print("[gdelt_tone] skipped along with gdelt")
    else:
        print("[gdelt_tone] pausing 10s before the next GDELT request...")
        time.sleep(10)
        try:
            print(f"[gdelt_tone] stored {fetch_gdelt_tone(con)} daily tone points")
        except Exception as e:
            print(f"[gdelt_tone] skipped: {str(e)[:120]}")
    print(f"Database now holds {con.execute('SELECT COUNT(*) FROM items').fetchone()[0]} items.")
    con.close()
    warnings_ = quality_checks(load(all_dates=True)[0])
    if warnings_:
        print("\nWorth knowing:")
        for w in warnings_:
            print("  ! " + w)
        if any(not w.startswith(("Your filters are hiding", "Everything here was collected"))
               for w in warnings_):
            print("  See \"Tighten what gets collected\" in docs/GETTING_STARTED.md.")


def show_preview(kept, rejected, filtered=()):
    rng = random.Random(1)
    kept, rejected = sorted(set(kept)), sorted(set(rejected))  # one line per distinct item
    print("\nNothing has been stored. This is what your settings would do.")
    for reason in ("name", "name+context", "place+topic"):
        rows = [l for r, l in kept if r == reason]
        if rows:
            print(f"\nWOULD KEEP, {len(rows)} items ({REASONS[reason]}):")
            for l in rng.sample(rows, min(6, len(rows))):
                print("   " + l)
    if rejected:
        print(f"\nWOULD REJECT, {len(rejected)} items (off-topic):")
        for l in rng.sample(rejected, min(8, len(rejected))):
            print("   " + l)
    filtered = sorted(set(filtered))
    if filtered:
        print(f"\nWOULD FILTER OUT, {len(filtered)} items (your filters):")
        for l in rng.sample(filtered, min(8, len(filtered))):
            print("   " + l)
    print("\nRead the lists.\n"
          "  Unrelated items under WOULD KEEP?  Add a start_date, add exclude_terms, or set\n"
          "                                     \"strict\": true under relevance.\n"
          "  Real items under WOULD REJECT?     Add their wording to place_terms or topic_terms.\n"
          "  Spam, bots or reposts being kept?  Turn on filters (docs/CONFIG.md, \"Filters\").\n"
          "  Real items being filtered out?     Loosen or remove that filter.\n"
          "Edit project.json, run the preview again, and when the lists look right run:\n"
          "  python sentiment_tracker.py collect")


def quality_checks(items):
    """Plain-language warnings about settings that look too loose. `items` should
    include everything stored, whatever the start date."""
    out = []
    got = [i for i in items if i["source"] in COLLECTED_SOURCES]
    if not got:
        return out
    dates = sorted(i["published"][:10] for i in got)
    span_weeks = (datetime.strptime(dates[-1], "%Y-%m-%d") -
                  datetime.strptime(dates[0], "%Y-%m-%d")).days // 7
    if not START_DATE and span_weeks > 12:
        out.append(f"Collected items go back {span_weeks} weeks, to {dates[0]}, and no start_date "
                   f"is set. If public discussion of the project began later than that, the "
                   f"older items are probably unrelated. Add \"start_date\": \"YYYY-MM\" to "
                   f"project.json with the month discussion began.")
    hidden = sum(1 for i in got if not after_start(i["published"]))
    if hidden:
        out.append(f"{hidden} stored items are older than your start_date and are being left "
                   "out. Run `prune` to see them and `prune --apply` to remove them.")
    live = [i for i in got if after_start(i["published"])]
    name_only = [i for i in live if i["kept"] == "name"]
    if len(live) >= 10 and len(name_only) > len(live) / 2:
        out.append(f"{len(name_only)} of {len(live)} collected items were kept on the project's "
                   "name alone, with no place or topic word. Skim them in the dashboard "
                   "(filter \"Kept because\"). If many are unrelated, set \"strict\": true "
                   "under relevance, or add exclude_terms.")
    stale = [i for i in live if i["kept"] is None]
    if stale:
        out.append(f"{len(stale)} stored items no longer fit your settings. Run `prune` to see "
                   "them and `prune --apply` to remove them.")
    hidden_f = apply_filters([dict(i) for i in live])[1]
    if hidden_f:
        out.append(f"Your filters are hiding {len(hidden_f)} of {len(live)} collected items "
                   f"({filter_summary(hidden_f)}). This is a note, not a fault: `prune` lists them.")
    elif not any([FILTERS["languages"], FILTERS["max_hashtags"] is not None,
                  FILTERS["collapse_reposts"]]):
        tags = sum(1 for i in live if i["source"] in SOCIAL_SOURCES and
                   len(re.findall(r"#\w+", " ".join(x["text"] for x in i["sentences"]))) > 3)
        social = sum(1 for i in live if i["source"] in SOCIAL_SOURCES)
        if social >= 10 and tags > social / 5:
            out.append(f"{tags} of {social} social posts carry more than 3 hashtags, which is "
                       "typical of bots and promotion. No filters are on: see \"Filters\" in "
                       "docs/CONFIG.md.")
    if len(live) >= 10 and not any(i["source"] not in COLLECTED_SOURCES for i in items):
        out.append("Everything here was collected from news and social media, and no local "
                   "records have been imported. That shows how the project is being covered and "
                   "discussed online, not what local people think. Import meeting minutes, "
                   "survey answers or copied comments to change that (see \"Add local records\" "
                   "in docs/GETTING_STARTED.md).")
    if not TOPIC_TERMS:
        out.append("No topic_terms are set, so coverage that doesn't use the project's name is "
                   "missed, and loose mentions of the name can't be told apart from real ones.")
    by = {}
    for i in live:
        by[i["source"]] = by.get(i["source"], 0) + 1
    if len(live) >= 20:
        top = max(by, key=by.get)
        if by[top] > 0.85 * len(live):
            out.append(f"{by[top]} of {len(live)} collected items come from {top}. One source is "
                       "setting the picture; import local records to balance it.")
    return out


def import_file(path, source, date):
    try:
        pub = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        raise SystemExit("--date must be YYYY-MM-DD")
    if not os.path.exists(path):
        raise SystemExit(f"File not found: {path}")
    con = db()
    c = {"new": 0, "duplicate": 0, "empty": 0}
    base = os.path.basename(path)
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "text" not in (reader.fieldnames or []):
                raise SystemExit("CSV needs a 'text' column (optional: date, speaker, url).")
            for i, row in enumerate(reader):
                d = (row.get("date") or "").strip()
                try:
                    p = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat() if d else pub
                except ValueError:
                    p = pub
                url = (row.get("url") or "").strip() or f"file://{base}#{i}"
                c[save_item(con, source, url, "", row.get("text", ""), p,
                            speaker=(row.get("speaker") or "").strip())] += 1
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            body = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if len(p.split()) >= 10]
        for i, para in enumerate(paras):
            c[save_item(con, source, f"file://{base}#{i}", "", para, pub)] += 1
    con.commit()
    con.close()
    print(f"Imported from {path} as '{source}' (weight {weight_of(source)}): "
          f"{c['new']} new, {c['duplicate']} duplicates")


# ==================================================================== statistics
def week_of(iso):
    try:  # date part only: works for every timestamp format the sources use
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d")
    except ValueError:
        d = datetime.now(timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def weighted_mean(pairs):
    tw = sum(w for _, w in pairs)
    return sum(s * w for s, w in pairs) / tw if tw else 0.0


def bootstrap_ci(pairs, seed):
    """90% interval for the weighted mean, resampling items."""
    if len(pairs) < THIN:
        return None
    rng = random.Random(seed)
    means = sorted(weighted_mean([rng.choice(pairs) for _ in pairs]) for _ in range(BOOTSTRAP))
    return means[int(0.05 * BOOTSTRAP)], means[int(0.95 * BOOTSTRAP) - 1]


def load(all_dates=False):
    """Everything the reports and dashboard need, in plain Python structures."""
    con = db()
    items = {}
    for r in con.execute("SELECT id, source, url, title, text, published, overall, scorer, "
                         "fulltext, speaker, lang FROM items"):
        if not all_dates and not after_start(r[5]):
            continue
        items[r[0]] = {"id": r[0], "source": r[1], "url": r[2], "title": r[3],
                       "published": r[5], "week": week_of(r[5]), "overall": r[6],
                       "scorer": r[7], "fulltext": bool(r[8]), "speaker": r[9] or "",
                       "lang": r[10] or "",
                       "kept": relevance_reason(f"{r[3]} {r[4]}"[:5000])
                       if r[1] in COLLECTED_SOURCES else "imported",
                       "weight": weight_of(r[1]),
                       "themes": {}, "sentences": []}
    for iid, t, s, n in con.execute("SELECT item_id, theme, score, n_sentences FROM theme_scores"):
        if iid in items:
            items[iid]["themes"][t] = round(s, 4)
    for iid, idx, text, s, th, ev in con.execute(
            "SELECT item_id, idx, text, score, themes, evidence FROM sentences ORDER BY item_id, idx"):
        if iid in items:
            items[iid]["sentences"].append({"text": text, "score": s,
                                            "themes": [x for x in th.split(",") if x],
                                            "evidence": json.loads(ev or "[]")})
    tone = con.execute("SELECT date, AVG(tone) FROM gdelt_tone GROUP BY date ORDER BY date").fetchall()
    con.close()
    out = list(items.values())
    if not all_dates:
        out = apply_filters(out)[0]
    return out, tone


def stats_by(items, key_of):
    """Group items by key_of(item) (None = leave out) and theme; theme 'ALL' uses
    each item's overall score. Returns {(key, theme): {...}}."""
    groups = {}
    for it in items:
        k = key_of(it)
        if k is None:
            continue
        groups.setdefault((k, "ALL"), []).append((it["overall"], it["weight"], it["id"]))
        for t, sc in it["themes"].items():
            groups.setdefault((k, t), []).append((sc, it["weight"], it["id"]))
    stats = {}
    for (k, t), rows in groups.items():
        pairs = [(sc, w) for sc, w, _ in rows]
        tw = sum(w for _, w in pairs)
        seed = int(hashlib.md5(f"{k}{t}".encode()).hexdigest()[:8], 16)
        stats[(k, t)] = {
            "week": k, "theme": t, "mean": weighted_mean(pairs), "n": len(rows),
            "unweighted": sum(sc for sc, _ in pairs) / len(pairs), "ci": bootstrap_ci(pairs, seed),
            "pos": sum(1 for sc, _ in pairs if sc >= NEUTRAL_BAND),
            "neg": sum(1 for sc, _ in pairs if sc <= -NEUTRAL_BAND),
            "shares": {iid: w / tw for _, w, iid in rows},
        }
    return stats


def weekly_stats(items):
    return stats_by(items, lambda it: it["week"])


def all_weeks(items):
    """Every ISO week from the first item to now, including weeks with no items."""
    days = [datetime.strptime(i["published"][:10], "%Y-%m-%d") for i in items
            if re.match(r"\d{4}-\d\d-\d\d", i["published"] or "")]
    if not days:
        return sorted({i["week"] for i in items})
    d, end, out = min(days), max(max(days), datetime.now()), []
    while week_of(d.strftime("%Y-%m-%d")) != week_of(end.strftime("%Y-%m-%d")):
        out.append(week_of(d.strftime("%Y-%m-%d")))
        d += timedelta(days=7)
    out.append(week_of(end.strftime("%Y-%m-%d")))
    return list(dict.fromkeys(out))


def period_stats(items, weeks):
    """Stats for the last 4 weeks, the 4 before, and all time."""
    last4, prev4 = set(weeks[-4:]), set(weeks[-8:-4])
    out = {}
    out.update(stats_by(items, lambda it: "LAST4" if it["week"] in last4 else None))
    out.update(stats_by(items, lambda it: "PREV4" if it["week"] in prev4 else None))
    out.update(stats_by(items, lambda it: "ALLTIME"))
    return out


# ======================================================================= reports
def report():
    items, tone = load()
    if not items:
        print("No data yet. Run `collect`, `import` or `demo` first.")
        return
    stats = weekly_stats(items)
    scorers = sorted({i["scorer"] for i in items})
    if len(scorers) > 1:
        print(f"WARNING: mixed scorers {scorers}; scores are not comparable.")
    by_src = {}
    for i in items:
        by_src[i["source"]] = by_src.get(i["source"], 0) + 1
    print("Items by source (weight): " +
          ", ".join(f"{s}={n} (x{weight_of(s)})" for s, n in sorted(by_src.items())))
    print(f"\n{'week':<9} {'theme':<14} {'weighted':>8} {'90% range':>16} {'n':>4}  +/-")
    for (wk, t) in sorted(stats, key=lambda k: (k[0], k[1] != "ALL", k[1])):
        s = stats[(wk, t)]
        ci = f"{s['ci'][0]:+.2f}..{s['ci'][1]:+.2f}" if s["ci"] else "thin data"
        print(f"{wk:<9} {t:<14} {s['mean']:>+8.2f} {ci:>16} {s['n']:>4}  {s['pos']}/{s['neg']}")
    with open("weekly_report.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week", "theme", "weighted_mean", "unweighted_mean", "ci90_low",
                    "ci90_high", "n_items", "n_positive", "n_negative", "label"])
        for k in sorted(stats):
            s = stats[k]
            w.writerow([s["week"], s["theme"], round(s["mean"], 3), round(s["unweighted"], 3),
                        round(s["ci"][0], 3) if s["ci"] else "", round(s["ci"][1], 3) if s["ci"] else "",
                        s["n"], s["pos"], s["neg"], label(s["mean"]) if s["ci"] else "thin data"])
    with open("evidence.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week", "source", "source_weight", "item_id", "item_title", "url",
                    "sentence", "sentence_score", "themes", "word_effects"])
        for it in sorted(items, key=lambda x: x["published"]):
            for s in it["sentences"]:
                w.writerow([it["week"], it["source"], it["weight"], it["id"], it["title"],
                            it["url"], s["text"], round(s["score"], 3), "|".join(s["themes"]),
                            "; ".join(f"{e[1]} {e[2]:+.2f}" for e in s["evidence"])])
    print("\nWrote weekly_report.csv and evidence.csv (every sentence, score and word effect).")


def summary():
    items, _ = load()
    end = datetime.now(timezone.utc)
    this_wk, last_wk = week_of(end.isoformat()), week_of((end - timedelta(days=7)).isoformat())
    stats = weekly_stats(items)
    lines = [f"# {PROJECT_NAME} sentiment: {this_wk} vs {last_wk}", "",
             "| Theme | This week | 90% range | n | Last week | n | Change | Read |",
             "|---|---|---|---|---|---|---|---|"]
    for t in ["ALL"] + list(THEMES):
        a, b = stats.get((this_wk, t)), stats.get((last_wk, t))
        fa = f"{a['mean']:+.2f}" if a else "-"
        ci = f"{a['ci'][0]:+.2f}..{a['ci'][1]:+.2f}" if a and a["ci"] else "thin"
        fb = f"{b['mean']:+.2f}" if b else "-"
        chg, read = "-", ""
        if a and b:
            chg = f"{a['mean'] - b['mean']:+.2f}"
            if a["ci"] and b["ci"]:
                overlap = a["ci"][0] <= b["ci"][1] and b["ci"][0] <= a["ci"][1]
                read = "within noise" if overlap else "likely shift"
            else:
                read = "too few items to tell"
        lines.append(f"| {t} | {fa} | {ci} | {a['n'] if a else 0} | {fb} | "
                     f"{b['n'] if b else 0} | {chg} | {read} |")
    recent = [i for i in items if i["week"] == this_wk]
    for name, rev, keep in (("Most negative", False, lambda s: s < -NEUTRAL_BAND),
                            ("Most positive", True, lambda s: s > NEUTRAL_BAND)):
        lines += ["", f"## {name} items this week"]
        for it in sorted([i for i in recent if keep(i["overall"])],
                         key=lambda i: i["overall"], reverse=rev)[:3]:
            top = min(it["sentences"], key=lambda s: s["score"]) if not rev else \
                max(it["sentences"], key=lambda s: s["score"])
            words = ", ".join(f"{e[1]} {e[2]:+.2f}" for e in top["evidence"][:4])
            lines.append(f"- {it['overall']:+.2f} [{it['source']}] "
                         f"\"{top['text'][:160]}\" (driven by: {words or 'no single word'}) {it['url']}")
    lines += ["", "Online and news discourse only; not a measure of local opinion."]
    with open("weekly_summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines) + "\n\nWrote weekly_summary.md")


def sample(n=10, theme=None):
    items, _ = load()
    pool = [i for i in items if not theme or theme in i["themes"]]
    for it in random.sample(pool, min(n, len(pool))):
        sc = it["themes"][theme] if theme else it["overall"]
        print(f"\n{sc:+.2f} {label(sc):<8} [{it['source']}] {(it['title'] or it['speaker'])[:100]}")
        for s in it["sentences"][:6]:
            if theme and theme not in s["themes"]:
                continue
            words = ", ".join(f"{e[1]} {e[2]:+.2f}" for e in s["evidence"][:4])
            print(f"   {s['score']:+.2f}  {s['text'][:140]}" + (f"\n          words: {words}" if words else ""))
    print("\nWould a person agree with each label? If many are wrong, edit THEMES / "
          "DOMAIN_LEXICON or try SCORER=transformer.")


def prune(apply=False):
    """Find collected items that the current settings would not keep (off-topic,
    or before start_date). Imported files are never touched."""
    con = db()
    marks = ",".join("?" * len(COLLECTED_SOURCES))
    rows = con.execute(f"SELECT id, source, title, text, published FROM items "
                       f"WHERE source IN ({marks})", COLLECTED_SOURCES).fetchall()
    drop = [r + ("before start date" if not after_start(r[4]) else "off-topic",) for r in rows
            if not after_start(r[4]) or not is_relevant(f"{r[2]} {r[3]}"[:5000])]
    gone = {r[0] for r in drop}
    by_id = {r[0]: r for r in rows}
    for it in apply_filters([i for i in load(all_dates=True)[0] if i["id"] in by_id
                             and i["id"] not in gone])[1]:
        drop.append(by_id[it["id"]] + (FILTER_NAMES[it["hidden"]],))
    for iid, source, title, text, pub, why in drop[:40]:
        print(f"  [{source}] {pub[:10]}  {' '.join((title or text).split())[:95]}   <- {why}")
    if len(drop) > 40:
        print(f"  ... and {len(drop) - 40} more")
    if not drop:
        print(f"Nothing to remove: all {len(rows)} collected items fit the current settings.")
    elif len(drop) == len(rows) and not apply:
        print(f"\nAll {len(rows)} collected items fail the current settings. That usually means "
              "the settings are wrong, not the items.\nCheck project.json before removing "
              "anything. Nothing has been removed.")
    elif not apply:
        print(f"\n{len(drop)} of {len(rows)} collected items do not fit the current settings. "
              "Nothing has been removed.\nIf the list looks right, run: "
              "python sentiment_tracker.py prune --apply")
    else:
        for r in drop:
            for table, col in (("items", "id"), ("sentences", "item_id"), ("theme_scores", "item_id")):
                con.execute(f"DELETE FROM {table} WHERE {col}=?", (r[0],))
        con.commit()
        print(f"\nRemoved {len(drop)} items. {len(rows) - len(drop)} collected items remain.")
    con.close()


def export_labels(n, path):
    """Write N random items to a CSV with an empty your_label column to fill in."""
    items, _ = load()
    pick = random.sample(items, min(n, len(items)))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "source", "text", "your_label"])
        for it in pick:
            w.writerow([it["id"], it["source"],
                        " ".join(x["text"] for x in it["sentences"])[:600], ""])
    print(f"Wrote {path} with {len(pick)} items. Fill in your_label with positive, negative "
          f"or neutral (p / n / 0 also work), without looking at the dashboard, then run:\n"
          f"  python sentiment_tracker.py validate {path}")


def norm_label(x):
    x = (x or "").strip().lower()
    if not x:
        return None
    if x.startswith("neu") or x in ("0", "="):
        return "neutral"
    if x[0] in "p+":
        return "positive"
    if x[0] in "n-":
        return "negative"
    return None


def validate(path):
    """Compare your labels with the model's current labels."""
    items = {i["id"]: i for i in load()[0]}
    pairs, skipped = [], 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            mine, it = norm_label(row.get("your_label")), items.get(row.get("item_id"))
            if mine is None or it is None:
                skipped += 1
                continue
            pairs.append((mine, label(it["overall"]), it))
    if not pairs:
        raise SystemExit("No labelled rows found. Fill in the your_label column first.")
    agree = sum(1 for a, b, _ in pairs if a == b)
    opposite = sum(1 for a, b, _ in pairs if {a, b} == {"positive", "negative"})
    print(f"Labelled items: {len(pairs)}" + (f" ({skipped} rows skipped)" if skipped else ""))
    print(f"Model agrees with you on {agree} of {len(pairs)} ({100 * agree / len(pairs):.0f}%).")
    print(f"Opposite calls (positive vs negative): {opposite} ({100 * opposite / len(pairs):.0f}%).")
    names = ["positive", "neutral", "negative"]
    print("\nRows = your label, columns = model label")
    print(" " * 10 + "".join(f"{n:>10}" for n in names))
    for a in names:
        print(f"{a:<10}" + "".join(f"{sum(1 for x, y, _ in pairs if x == a and y == b):>10}"
                                   for b in names))
    wrong = [(a, b, it) for a, b, it in pairs if a != b]
    if wrong:
        print("\nDisagreements:")
        for a, b, it in wrong[:15]:
            text = " ".join(x["text"] for x in it["sentences"])[:150]
            print(f"  you: {a:<8} model: {b:<8} ({it['overall']:+.2f})  {text}")
    if len(pairs) < 30:
        print("\nFewer than 30 labels: treat these percentages as a rough guide.")
    neutral = sum(1 for a, _, _ in pairs if a == "neutral")
    print(f"\nFor comparison, calling every item neutral would agree on {neutral} of {len(pairs)} "
          f"({100 * neutral / len(pairs):.0f}%). The model is only earning its keep if it beats that.")
    print("If opposite calls are common, add words to \"lexicon\" in project.json or try "
          "SCORER=transformer, run `rescore`, then run validate again.")


# ===================================================================== dashboard
def dashboard(open_it=True, path="dashboard.html"):
    items, tone = load()
    if not items:
        print("No data yet. Run `collect`, `import` or `demo` first.")
        return
    weeks = all_weeks(items)
    stats = {**weekly_stats(items), **period_stats(items, weeks)}
    by_src = {}
    for i in items:
        d = by_src.setdefault(i["source"], {"n": 0, "w": weight_of(i["source"])})
        d["n"] += 1
    tw = sum(d["n"] * d["w"] for d in by_src.values())
    for d in by_src.values():
        d["share"] = d["n"] * d["w"] / tw
    data = {
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "scorers": sorted({i["scorer"] for i in items}),
        "themes": list(THEMES), "theme_keywords": THEMES, "weeks": weeks,
        "last4": weeks[-4:], "version": VERSION,
        "thin": THIN, "neutral": NEUTRAL_BAND, "sources": by_src,
        "weights": {**SOURCE_WEIGHTS, "(any other)": DEFAULT_WEIGHT},
        "lexicon": DOMAIN_LEXICON,
        "stats": [{**{k: v for k, v in s.items() if k != "shares"}} for s in stats.values()],
        "shares": {f"{s['week']}|{s['theme']}": s["shares"] for s in stats.values()},
        "items": [{**i, "sentences": i["sentences"]} for i in items],
        "checks": quality_checks(load(all_dates=True)[0]) if CONFIGURED else
        ["No project.json was found, so this dashboard is using example settings. "
         "Run `python sentiment_tracker.py init` to set up your project."],
        "reasons": REASONS,
        "filter_note": (lambda h: f"Filters hid {len(h)} collected items: {filter_summary(h)}."
                        if h else "")(apply_filters(
                            [i for i in load(all_dates=True)[0] if after_start(i["published"])])[1]),
        "tone": [{"date": d, "week": week_of(d + "T00:00:00+00:00"), "tone": t} for d, t in tone],
    }
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    with open(path, "w", encoding="utf-8") as f:
        f.write(DASHBOARD_HTML.replace("__PROJECT__", htmllib.escape(PROJECT_NAME))
                .replace("__AREA__", htmllib.escape(AREA_NAME)).replace("__DATA__", payload))
    print(f"Wrote {path} ({len(items)} items, {len(weeks)} weeks)")
    if open_it:
        webbrowser.open("file://" + os.path.abspath(path))


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__PROJECT__ sentiment</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a66;--line:#e4e2dc;--pos:#1f7a4d;--neg:#b3362f;--neu:#8a877f;--accent:#2f5d8a}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:20px 28px 12px;border-bottom:1px solid var(--line);background:var(--card)}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}.mute{color:var(--mute)}
.banner{margin-top:10px;padding:8px 12px;background:#fff7e6;border:1px solid #f0d9a8;border-radius:6px;font-size:13px}
main{padding:20px 28px;display:grid;gap:18px;max-width:1400px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.kpi{border:1px solid var(--line);border-radius:8px;padding:12px;cursor:pointer;background:var(--card)}
.kpi.sel{outline:2px solid var(--accent)}.kpi .t{font-size:12px;color:var(--mute);text-transform:uppercase;letter-spacing:.03em}
.kpi .v{font-size:26px;font-weight:650;margin:2px 0}.kpi .s{font-size:12px;color:var(--mute)}
.pos{color:var(--pos)}.neg{color:var(--neg)}.neu{color:var(--neu)}
.row{display:grid;grid-template-columns:2fr 1fr;gap:18px}@media(max-width:1000px){.row{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--mute)}td.num{text-align:right;font-variant-numeric:tabular-nums}
.legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;font-size:12px}.legend label{cursor:pointer;display:flex;align-items:center;gap:4px}
.sw{display:inline-block;width:12px;height:3px;border-radius:2px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}select,input{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:#fff}
.item{border:1px solid var(--line);border-radius:8px;margin-bottom:8px}
.ihead{display:grid;grid-template-columns:70px 1fr auto;gap:10px;padding:10px 12px;cursor:pointer;align-items:start}
.badge{font-weight:650;font-variant-numeric:tabular-nums;font-size:15px}.meta{font-size:12px;color:var(--mute)}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:10px;background:#eef2f6;color:#2f4a63;margin-right:4px}
.ibody{display:none;padding:4px 12px 12px;border-top:1px solid var(--line)}.item.open .ibody{display:block}
.sent{padding:8px 0;border-bottom:1px dashed var(--line)}.sent:last-child{border-bottom:0}
.sent .sc{font-weight:600;font-variant-numeric:tabular-nums;margin-right:6px}
.w{border-radius:3px;padding:0 2px}.ev{font-size:12px;color:var(--mute);margin-top:3px}
.contrib{font-size:12px;background:#f3f6f9;border-radius:6px;padding:6px 8px;margin:8px 0}
details{margin-top:6px}summary{cursor:pointer;color:var(--accent)}
.bar{height:8px;background:#eee;border-radius:4px;overflow:hidden;width:120px;display:inline-block;vertical-align:middle}
.bar i{display:block;height:100%;background:var(--accent)}
svg text{font-size:11px;fill:#6b6a66}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{font:inherit;font-size:13px;padding:5px 12px;border:0;background:#fff;cursor:pointer;color:var(--mute)}
.seg button+button{border-left:1px solid var(--line)}.seg button.on{background:var(--accent);color:#fff}
</style></head><body>
<header><h1>__PROJECT__: community sentiment</h1>
<div class="mute" id="sub"></div>
<div class="banner" id="checks" style="display:none;background:#fdecea;border-color:#f1b5ae"></div>
<div class="banner">This measures online and news discourse, not the opinion of everyone in __AREA__. Vocal people and outsiders are over-represented. Every number below can be traced to the sentences and words behind it.</div></header>
<main>
<section class="card"><div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px"><h2 style="margin:0">Sentiment by theme <span class="mute" id="wkl" style="font-weight:400"></span></h2><div class="seg" id="seg"></div></div>
<p id="take" style="margin:0 0 12px"></p><div class="kpis" id="kpis"></div>
<p class="mute" style="margin:10px 0 0;font-size:12px">Score from -1 (negative) to +1 (positive), weighted by source. The range is a 90% bootstrap interval. "Thin" means fewer than <span class="thinN"></span> items: no range is shown, the number is greyed out, and it should be read as an anecdote. Click a card to filter the evidence.</p></section>
<div class="row">
<section class="card"><h2>Weekly trend</h2><div id="chart"></div><div class="legend" id="legend"></div>
<p class="mute" style="font-size:12px;margin:8px 0 0">Hollow points have fewer than <span class="thinN"></span> items. The shaded band is the 90% range for the selected theme.</p>
<div id="tone"></div></section>
<section class="card"><h2>Weights in use</h2><table id="wtab"></table>
<details><summary>How scores and weights are calculated</summary><ol style="padding-left:18px;font-size:13px">
<li>Each item is split into sentences, and sentences are split at "but", "however" and similar, so mixed views are scored part by part.</li>
<li>Each sentence is scored from -1 to +1 by <b id="scn"></b>.</li>
<li><b>Word effects:</b> each word is removed in turn and the sentence re-scored. The difference is that word's measured effect. Green words pushed the score up, red pushed it down. This includes rules such as "not" flipping the next word.</li>
<li>A sentence counts toward a theme when it contains one of the theme's keywords. An item's theme score is the mean of those sentences.</li>
<li>An item's overall score is the mean of all its sentences, so long articles don't swing to the extremes.</li>
<li>Weekly figure = sum(item score x source weight) / sum(weights). Each item counts once. Its share is shown on each item.</li>
<li>90% range: items are resampled 1,000 times and the middle 90% of the results is reported. Overlapping ranges between weeks mean the change could be noise.</li></ol></details>
<details><summary>Theme patterns and extra word scores</summary><div id="kw" style="font-size:12px"></div></details></section>
</div>
<section class="card"><h2>Evidence explorer</h2>
<div class="filters">
<select id="fWeek"></select><select id="fTheme"></select><select id="fSrc"></select>
<select id="fPol"><option value="">All tones</option><option value="pos">Positive</option><option value="neg">Negative</option><option value="neu">Neutral</option></select>
<select id="fKept"><option value="">Kept because: any</option></select>
<select id="fSort"><option value="share">Largest influence first</option><option value="neg">Most negative first</option><option value="pos">Most positive first</option><option value="new">Newest first</option></select>
<input id="fQ" placeholder="Search text" size="18"></div>
<div class="mute" id="count" style="margin-bottom:8px"></div><div id="list"></div></section>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const COLORS=['#2f5d8a','#b3362f','#d08a1e','#1f7a4d','#7b4fa0','#4a8c9c','#a0522d'];
const TH=['ALL',...D.themes];const col=t=>t==='ALL'?'#1d1d1b':COLORS[D.themes.indexOf(t)%COLORS.length];
const nice=t=>t==='ALL'?'All items':t.replace('_',' & ').replace(/\b\w/g,c=>c.toUpperCase());
const cls=v=>v>=D.neutral?'pos':v<=-D.neutral?'neg':'neu';const f2=v=>{const r=Math.round(v*100)/100;return r===0?'0.00':(r>0?'+':'')+r.toFixed(2)};
const el=(tag,attrs={},...kids)=>{const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else e.setAttribute(k,v)}kids.forEach(k=>k&&e.append(k));return e};
const S={};D.stats.forEach(s=>S[s.week+'|'+s.theme]=s);
const state={theme:'ALL',week:D.weeks[D.weeks.length-1],period:'last4',on:new Set(TH)};
const PER={week:{name:'Latest week',key:()=>state.week,prev:()=>D.weeks[D.weeks.indexOf(state.week)-1],label:()=>state.week,plabel:p=>p},
 last4:{name:'Last 4 weeks',key:()=>'LAST4',prev:()=>'PREV4',label:()=>D.last4[0]+' to '+D.last4[D.last4.length-1],plabel:()=>'previous 4 weeks'},
 all:{name:'All time',key:()=>'ALLTIME',prev:()=>null,label:()=>D.weeks[0]+' to '+D.weeks[D.weeks.length-1],plabel:()=>''}};
document.querySelectorAll('.thinN').forEach(e=>e.textContent=D.thin);
document.getElementById('scn').textContent=D.scorers.join(', ').replace('vader','VADER (rule-based, plus the extra word scores listed below)');
const tot=D.items.length;
if(D.checks.length){const c=document.getElementById('checks');c.style.display='block';if(D.checks.every(x=>/^(Your filters are hiding|Everything here was collected)/.test(x))){c.style.background='#eef3f8';c.style.borderColor='#c5d5e6'}c.append(el('b',{text:'Worth knowing'}));const ul=el('ul',{style:'margin:6px 0 0;padding-left:18px'});D.checks.forEach(x=>ul.append(el('li',{text:x})));c.append(ul)}
if(D.filter_note)document.getElementById('sub').textContent+=' · '+D.filter_note;
const keptLabel=k=>D.reasons[k]||'no longer fits your settings';
document.getElementById('sub').textContent=`${tot} items, ${D.weeks[0]} to ${D.weeks[D.weeks.length-1]} · scorer: ${D.scorers.join(', ')} · built ${D.generated} · v${D.version}`+(D.scorers.length>1?' · WARNING: mixed scorers, scores not comparable':'');

function kpis(){const P=PER[state.period],wk=P.key(),prev=P.prev();document.getElementById('wkl').textContent='('+P.label()+')';
 const seg=document.getElementById('seg');seg.innerHTML='';Object.entries(PER).forEach(([k,v])=>{const b=el('button',{class:state.period===k?'on':'',text:v.name});b.onclick=()=>{state.period=k;kpis()};seg.append(b)});
 const read=D.themes.map(t=>S[wk+'|'+t]).filter(s=>s&&s.ci).sort((a,b)=>b.mean-a.mean),thinN=D.themes.length-read.length,tk=document.getElementById('take');
 if(!read.length)tk.textContent='Too few items in this period to read any theme. Try a longer period, or import local records.';
 else{const hi=read[0],lo=read[read.length-1];tk.textContent=(read.length>1?`Most positive theme: ${nice(hi.theme)} (${f2(hi.mean)}). Most negative: ${nice(lo.theme)} (${f2(lo.mean)}).`:`Only ${nice(hi.theme)} has enough items to read (${f2(hi.mean)}).`)+(thinN?` ${thinN} of ${D.themes.length} themes have too few items to read.`:'')}
 const box=document.getElementById('kpis');box.innerHTML='';
 TH.forEach(t=>{const s=S[wk+'|'+t],p=prev&&S[prev+'|'+t];const k=el('div',{class:'kpi'+(state.theme===t?' sel':'')});
  k.append(el('div',{class:'t',text:nice(t)}));
  if(!s){k.append(el('div',{class:'v neu',text:'–'}),el('div',{class:'s',text:'no items in this period'}))}
  else{k.append(el('div',{class:'v '+(s.ci?cls(s.mean):'neu'),text:f2(s.mean)}));
   k.append(el('div',{class:'s',text:s.ci?`90% range ${f2(s.ci[0])} to ${f2(s.ci[1])}`:'thin data, treat as noise'}));
   k.append(el('div',{class:'s',text:`${s.n} item${s.n===1?'':'s'} · ${s.pos} positive / ${s.neg} negative`}));
   if(p){let r='';if(s.ci&&p.ci){r=(s.ci[0]<=p.ci[1]&&p.ci[0]<=s.ci[1])?'within noise':'likely shift'}else r='too few to tell';
    k.append(el('div',{class:'s',text:`vs ${P.plabel(prev)}: ${f2(s.mean-p.mean)} (${r})`}))}}
  k.onclick=()=>{state.theme=t;document.getElementById('fTheme').value=t==='ALL'?'':t;kpis();chart();list()};box.append(k)})}

function chart(){const W=820,H=300,L=40,R=34,T=10,B=40,wks=D.weeks;const x=i=>L+(wks.length<2?(W-L-R)/2:i*(W-L-R)/(wks.length-1)),y=v=>T+(1-v)/2*(H-T-B);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Weekly sentiment by theme">`;
 [-1,-.5,0,.5,1].forEach(v=>s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="${v===0?'#bbb':'#eee'}"/><text x="${L-6}" y="${y(v)+4}" text-anchor="end">${v>0?'+':''}${v}</text>`);
 const step=Math.ceil(wks.length/12);wks.forEach((w,i)=>{if(i%step===0)s+=`<text x="${x(i)}" y="${H-B+16}" text-anchor="middle">${w}</text>`});
 const t0=state.theme;const band=wks.map((w,i)=>[i,S[w+'|'+t0]]).filter(([,v])=>v&&v.ci);
 if(band.length>1){s+=`<path d="M${band.map(([i,v])=>x(i)+','+y(v.ci[1])).join('L')}L${band.slice().reverse().map(([i,v])=>x(i)+','+y(v.ci[0])).join('L')}Z" fill="${col(t0)}" opacity=".12"/>`}
 TH.filter(t=>state.on.has(t)).forEach(t=>{const pts=wks.map((w,i)=>[i,S[w+'|'+t]]).filter(([,v])=>v);if(!pts.length)return;const sel=t===t0;
  s+=`<polyline fill="none" stroke="${col(t)}" stroke-width="${sel?3:1.5}" opacity="${sel?1:.55}" points="${pts.map(([i,v])=>x(i)+','+y(v.mean)).join(' ')}"/>`;
  pts.forEach(([i,v])=>{const thin=v.n<D.thin;s+=`<circle cx="${x(i)}" cy="${y(v.mean)}" r="${sel?5:3.5}" fill="${thin?'#fff':col(t)}" stroke="${col(t)}" stroke-width="1.5"><title>${nice(t)} ${wks[i]}: ${f2(v.mean)}, n=${v.n}${v.ci?`, 90% ${f2(v.ci[0])} to ${f2(v.ci[1])}`:', thin data'}</title></circle>`})});
 document.getElementById('chart').innerHTML=s+'</svg>';
 const lg=document.getElementById('legend');lg.innerHTML='';TH.forEach(t=>{const cb=el('input',{type:'checkbox'});cb.checked=state.on.has(t);cb.onchange=()=>{cb.checked?state.on.add(t):state.on.delete(t);chart()};
  const l=el('label',{},cb,el('span',{class:'sw',style:`background:${col(t)}`}),document.createTextNode(nice(t)));lg.append(l)});
 const tn=document.getElementById('tone');if(!D.tone.length){tn.innerHTML='<p class="mute" style="font-size:12px">No GDELT tone data yet.</p>';return}
 const tw={};D.tone.forEach(r=>{(tw[r.week]=tw[r.week]||[]).push(r.tone)});const tk=Object.keys(tw).sort();const tv=tk.map(k=>tw[k].reduce((a,b)=>a+b)/tw[k].length);
 const mx=Math.max(1,...tv.map(Math.abs));const bw=(W-L-R)/tk.length;let g=`<h2 style="margin-top:14px">GDELT news tone <span class="mute" style="font-weight:400;font-size:12px">(GDELT's own scale, roughly -10 to +10; not mixed with the scores above)</span></h2><svg viewBox="0 0 ${W} 150" width="100%">`;
 const ty=v=>55-v/mx*45,tstep=Math.ceil(tk.length/10);g+=`<line x1="${L}" x2="${W-R}" y1="55" y2="55" stroke="#bbb"/>`;
 tk.forEach((k,i)=>{const v=tv[i];g+=`<rect x="${L+i*bw+bw*.15}" width="${bw*.7}" y="${Math.min(55,ty(v))}" height="${Math.abs(ty(v)-55)}" fill="${v>=0?'#1f7a4d':'#b3362f'}" opacity=".7"><title>${k}: ${f2(v)}</title></rect><text x="${L+i*bw+bw/2}" y="${v>=0?ty(v)-3:ty(v)+12}" text-anchor="middle">${Math.abs(v)<0.05?'0.0':v.toFixed(1)}</text>`+(i%tstep===0?`<text x="${L+i*bw+bw/2}" y="146" text-anchor="middle">${k}</text>`:'')});
 tn.innerHTML=g+'</svg>'}

function weights(){const t=document.getElementById('wtab');t.innerHTML='<tr><th>Source</th><th class="num">Weight</th><th class="num">Items</th><th>Share of total influence</th></tr>';
 Object.entries(D.sources).sort((a,b)=>b[1].share-a[1].share).forEach(([k,v])=>{const tr=el('tr');tr.append(el('td',{text:k}),el('td',{class:'num',text:'x'+v.w}),el('td',{class:'num',text:v.n}));
  const bar=el('span',{class:'bar'},el('i',{style:`width:${(v.share*100).toFixed(1)}%`}));const td=el('td');td.append(bar,document.createTextNode(' '+(v.share*100).toFixed(0)+'%'));tr.append(td);t.append(tr)});
 const unused=Object.entries(D.weights).filter(([k])=>!D.sources[k]).map(([k,v])=>k+' x'+v).join(', ');
 if(unused)t.append(el('tr',{},el('td',{colspan:4,class:'mute',text:'Set but no items yet: '+unused})));
 const kw=document.getElementById('kw');Object.entries(D.theme_keywords).forEach(([t,k])=>kw.append(el('p',{},el('b',{text:nice(t)+': '}),document.createTextNode(k.join(', ')))));
 kw.append(el('p',{},el('b',{text:'Extra word scores (-4 to +4): '}),document.createTextNode(Object.entries(D.lexicon).map(([w,v])=>w+' '+(v>0?'+':'')+v).join(', '))))}

function filters(){const fw=document.getElementById('fWeek');fw.append(el('option',{value:'',text:'All weeks'}));D.weeks.slice().reverse().forEach(w=>fw.append(el('option',{value:w,text:w})));fw.value='';
 const ft=document.getElementById('fTheme');ft.append(el('option',{value:'',text:'All themes'}));D.themes.forEach(t=>ft.append(el('option',{value:t,text:nice(t)})));
 const fs=document.getElementById('fSrc');fs.append(el('option',{value:'',text:'All sources'}));Object.keys(D.sources).sort().forEach(s=>fs.append(el('option',{value:s,text:s})));
 const fk=document.getElementById('fKept');[...new Set(D.items.map(i=>i.kept||''))].sort().forEach(k=>fk.append(el('option',{value:k||'none',text:'Kept because: '+keptLabel(k)})));
 ['fWeek','fTheme','fSrc','fPol','fKept','fSort','fQ'].forEach(id=>document.getElementById(id).addEventListener(id==='fQ'?'input':'change',()=>{if(id==='fWeek'&&fw.value){state.week=fw.value;state.period='week';kpis()}if(id==='fTheme'){state.theme=ft.value||'ALL';kpis();chart()}list()}))}

function sentenceNode(s,theme){const d=el('div',{class:'sent'});const inTheme=!theme||s.themes.includes(theme);if(!inTheme)d.style.opacity=.45;
 d.append(el('span',{class:'sc '+cls(s.score),text:f2(s.score)}));const ev={};s.evidence.forEach(e=>ev[e[0]]=e);
 s.text.trim().split(/\s+/).forEach((w,i)=>{if(ev[i]){const e=ev[i],a=Math.min(1,Math.abs(e[2])/.5);
   d.append(el('span',{class:'w',style:`background:${e[2]>0?`rgba(31,122,77,${.15+.45*a})`:`rgba(179,54,47,${.15+.45*a})`}`,title:`effect ${f2(e[2])}`+(e[3]!=null?`, lexicon value ${e[3]>0?'+':''}${e[3]}`:''),text:w}))}
  else d.append(document.createTextNode(w));d.append(document.createTextNode(' '))});
 s.themes.forEach(t=>d.append(el('span',{class:'tag',text:nice(t)})));
 if(s.evidence.length)d.append(el('div',{class:'ev',text:'Word effects: '+s.evidence.map(e=>`${e[1]} ${f2(e[2])}`+(e[3]!=null?` (lexicon ${e[3]>0?'+':''}${e[3]})`:'')).join(' · ')}));
 else d.append(el('div',{class:'ev',text:'No single word moved this score by 0.02 or more.'}));return d}

function list(){const wk=document.getElementById('fWeek').value,th=document.getElementById('fTheme').value,src=document.getElementById('fSrc').value,
 pol=document.getElementById('fPol').value,srt=document.getElementById('fSort').value,q=document.getElementById('fQ').value.toLowerCase();
 const kp=document.getElementById('fKept').value;
 let rows=D.items.filter(i=>(!wk||i.week===wk)&&(!th||i.themes[th]!==undefined)&&(!src||i.source===src)&&(!kp||(i.kept||'none')===kp));
 const val=i=>th?i.themes[th]:i.overall;rows=rows.filter(i=>!pol||cls(val(i))===pol);
 if(q)rows=rows.filter(i=>(i.title+' '+i.sentences.map(s=>s.text).join(' ')).toLowerCase().includes(q));
 const share=i=>{const m=D.shares[i.week+'|'+(th||'ALL')];return m?m[i.id]||0:0};
 rows.sort(srt==='neg'?(a,b)=>val(a)-val(b):srt==='pos'?(a,b)=>val(b)-val(a):srt==='new'?(a,b)=>b.published.localeCompare(a.published):(a,b)=>Math.abs(share(b)*val(b))-Math.abs(share(a)*val(a)));
 document.getElementById('count').textContent=`${rows.length} items`+(rows.length>200?' (showing first 200)':'');
 const L=document.getElementById('list');L.innerHTML='';
 rows.slice(0,200).forEach(i=>{const v=val(i),sh=share(i);const it=el('div',{class:'item'});
  const head=el('div',{class:'ihead'});head.append(el('div',{class:'badge '+cls(v),text:f2(v)}));
  const mid=el('div');const firstText=i.title||i.sentences.map(s=>s.text).join(' ');mid.append(el('div',{text:firstText.slice(0,200)}));
  mid.append(el('div',{class:'meta',text:(i.speaker?i.speaker+' · ':'')+`${i.source} · weight x${i.weight} · ${i.published.slice(0,10)} · ${i.week} · kept: ${keptLabel(i.kept)}`+(i.fulltext?' · full article':'')}));
  const tg=el('div');Object.keys(i.themes).forEach(t=>tg.append(el('span',{class:'tag',text:nice(t)+' '+f2(i.themes[t])})));mid.append(tg);head.append(mid);
  head.append(el('div',{class:'meta',style:'text-align:right',text:sh?`${(sh*100).toFixed(1)}% of ${nice(th||'ALL')} ${i.week}`:''}));
  const body=el('div',{class:'ibody'});
  if(sh){const s=S[i.week+'|'+(th||'ALL')];body.append(el('div',{class:'contrib',text:`Contribution: this item's ${th?nice(th):'overall'} score ${f2(v)} x weight ${i.weight} counts for ${(sh*100).toFixed(1)}% of the ${i.week} figure (${f2(s.mean)}, from ${s.n} items). It moves that figure by ${f2(sh*v)}.`}))}
  if(i.url&&/^https?:/.test(i.url))body.append(el('div',{class:'meta'},el('a',{href:i.url,target:'_blank',rel:'noopener',text:'Open source'})));
  i.sentences.forEach(s=>body.append(sentenceNode(s,th)));
  head.onclick=()=>it.classList.toggle('open');it.append(head,body);L.append(it)})}
filters();kpis();chart();weights();list();
</script></body></html>
"""


# ========================================================================== demo
DEMO = [
    ("Residents back the energy park for jobs",
     "The project could bring hundreds of jobs to the county. Downtown storefronts are empty and the economy needs this opportunity."),
    ("Neighbours worry about safety at the proposed site",
     "Some residents fear an accident near the river. Others say the safety record of the design is good."),
    ("Residents say the project was announced too fast",
     "People in town complained they were not notified sooner. The public meeting felt rushed and the pace worries them."),
    ("Who pays for the energy park?",
     "No cost estimate or financing plan has been released. Ratepayers fear overruns."),
    ("County leaders vote to support the application",
     "County leaders voted overwhelmingly to apply. They call it a great first step for the county."),
]


def demo():
    con = db()
    for i, (t, x) in enumerate(DEMO):
        save_item(con, "demo", f"demo://{i}", t, x, now_iso())
    con.commit()
    con.close()
    print(f"Loaded {len(DEMO)} fictional demo items into {DB_PATH}. "
          f"Delete {DB_PATH} before collecting real data.")


configure()  # neutral example settings until main() loads project.json


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", metavar="FILE", help="settings file (default project.json)")
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("init")
    n.add_argument("name", nargs="?", help='project name; leave out to be asked questions')
    n.add_argument("--type", default="other", choices=list(PROJECT_TYPES), dest="kind",
                   help="adds search and theme words for that kind of project")
    n.add_argument("--since", default="", metavar="YYYY-MM",
                   help="ignore anything published before this")
    n.add_argument("--strict", action="store_true",
                   help="the name alone is not enough; also need a place or topic word")
    n.add_argument("--no-filters", action="store_true",
                   help="do not switch on the recommended filters")
    n.add_argument("--exclude", nargs="*", default=[],
                   help='words that follow the name when it means something else, e.g. bank')
    n.add_argument("--area", default="", help='area name, e.g. "Marlow County"')
    n.add_argument("--place", nargs="*", default=[], help="other place names that identify it")
    n.add_argument("--topic", nargs="*", default=[], help='topic words, e.g. nuclear reactor')
    c = sub.add_parser("collect")
    c.add_argument("--headlines-only", action="store_true", help="skip fetching article text")
    c.add_argument("--preview", action="store_true",
                   help="store nothing; show what your settings would keep and reject")
    sub.add_parser("check")
    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--source", default="minutes")
    i.add_argument("--date", required=True, help="YYYY-MM-DD, used when rows have no date")
    d = sub.add_parser("dashboard")
    d.add_argument("--no-open", action="store_true")
    sub.add_parser("report")
    sub.add_parser("summary")
    sub.add_parser("demo")
    s = sub.add_parser("sample")
    s.add_argument("n", nargs="?", type=int, default=10)
    s.add_argument("--theme", choices=list(THEMES))
    s.add_argument("--export", metavar="FILE", help="write the sample to a CSV for labelling")
    v = sub.add_parser("validate")
    v.add_argument("file")
    sub.add_parser("rescore")
    pr = sub.add_parser("prune")
    pr.add_argument("--apply", action="store_true", help="actually remove the listed items")
    a = ap.parse_args()
    if a.cmd == "init":
        if not a.name:
            return init_wizard(a.project or "project.json")
        return init_project(a.name, a.area, a.place, a.topic, a.project or "project.json",
                            a.kind, a.exclude, a.since, a.strict, not a.no_filters)
    cfg = load_config(a.project)
    if a.project and cfg is None:
        raise SystemExit(f"Settings file not found: {a.project}")
    configure(cfg)
    if a.cmd in ("collect", "check", "prune") and not CONFIGURED:
        # Without settings every stored item would look off-topic, and prune
        # would offer to delete all of it.
        raise SystemExit("No project.json found in this folder, so there are no settings to "
                         "work with.\nSet your project up first by running:\n"
                         "  python sentiment_tracker.py init\nand answering the questions.")
    if a.cmd == "collect":
        collect(a.headlines_only, a.preview)
    elif a.cmd == "check":
        found = quality_checks(load(all_dates=True)[0])
        print("\n".join("! " + w for w in found) if found else
              "No problems found with what has been collected.")
    elif a.cmd == "import":
        import_file(a.file, a.source, a.date)
    elif a.cmd == "dashboard":
        dashboard(not a.no_open)
    elif a.cmd == "report":
        report()
    elif a.cmd == "summary":
        summary()
    elif a.cmd == "sample":
        export_labels(a.n, a.export) if a.export else sample(a.n, a.theme)
    elif a.cmd == "validate":
        validate(a.file)
    elif a.cmd == "rescore":
        rescore()
    elif a.cmd == "prune":
        prune(a.apply)
    elif a.cmd == "demo":
        demo()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
