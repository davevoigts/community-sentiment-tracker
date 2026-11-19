import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

import fakes

ROOT = Path(__file__).resolve().parents[1]

# A fictional project. No real place, company or scheme.
TEST_PROJECT = {
    "project_name": "Riverton Energy Park",
    "area_name": "Marlow County",
    "search": {"mastodon_tags": ["RivertonEnergyPark"]},
    "relevance": {"exclude_terms": ["riverton energy park bank"],
                  "place_terms": ["marlow county"], "topic_terms": ["energy", "power plant"]},
}


@pytest.fixture
def t(tmp_path, monkeypatch):
    """Fresh copy of the module, working in an empty temp folder, network faked."""
    spec = importlib.util.spec_from_file_location("tracker", ROOT / "sentiment_tracker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod.requests, "get", fakes.fake_get)
    monkeypatch.setattr(mod.requests, "post", fakes.fake_post)
    monkeypatch.setenv("BSKY_HANDLE", "me.example")
    monkeypatch.setenv("BSKY_APP_PASSWORD", "x")
    mod.configure(dict(TEST_PROJECT))
    return mod


def count(source=None):
    con = sqlite3.connect("tracker.db")
    q = "SELECT COUNT(*) FROM items" + (" WHERE source=?" if source else "")
    return con.execute(q, (source,) if source else ()).fetchone()[0]


# ---------------------------------------------------------------- text handling
def test_contrast_split(t):
    assert t.split_sentences("Good for jobs but nobody told us.") == ["Good for jobs", "nobody told us."]
    assert len(t.split_sentences("Residents fear an accident would be devastating, while engineers say it is safe.")) == 2
    assert len(t.split_sentences("While the meeting ran, people waited outside.")) == 1


def test_relevance(t):
    assert t.is_relevant("Riverton Energy Park gets county backing")
    assert t.is_relevant("Marlow County energy hearing")
    assert not t.is_relevant("Riverton Energy Park Bank opens a branch")
    assert not t.is_relevant("Marlow County fair this weekend")


def test_safety_is_not_praise(t):
    assert abs(t.scorer()("Towns weigh plant safety")) < 0.05


def test_mixed_comment_scored_per_theme(t):
    _, _, themes = t.analyse("Great for jobs but we were never consulted and it was rushed.")
    assert themes["jobs_economy"][0] > 0
    assert themes["transparency_trust"][0] < 0


def test_word_evidence_finds_driver(t):
    _, rows, _ = t.analyse("The hearing drew an angry crowd.")
    words = [e[1] for e in rows[0]["evidence"]]
    assert words[0] == "angry"


# ---------------------------------------------------------------- collection
def test_collect_dedupes_and_filters(t, capsys):
    t.collect()
    out = capsys.readouterr().out
    assert count("gdelt") == 2
    # GDELT a1 and the Example Gazette headline are the same story: kept once, as GDELT
    assert count("news") == 3
    assert count("mastodon") == 1      # off-topic post dropped
    assert count("bluesky") == 3
    assert "with full article text" in out
    before = count()
    t.collect()                         # a second run adds nothing
    assert count() == before


def test_bluesky_without_login(t, monkeypatch):
    monkeypatch.delenv("BSKY_HANDLE")
    with pytest.raises(RuntimeError, match="no login"):
        t.fetch_bluesky()


def test_gdelt_retries_on_429(t, monkeypatch):
    calls = {"n": 0}

    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return fakes.FakeResponse("busy", 429)
        return fakes.fake_get(url, **kw)
    monkeypatch.setattr(t.requests, "get", flaky)
    assert len(t.fetch_gdelt()) == 4
    assert calls["n"] >= 2


def test_old_database_is_moved_aside(t):
    con = sqlite3.connect("tracker.db")
    con.execute("CREATE TABLE items(id TEXT, source TEXT)")
    con.commit()
    con.close()
    t.db().close()
    assert os.path.exists("tracker_v2_backup.db")


# ---------------------------------------------------------------- imports
def test_import_csv_speakers_not_scored(t):
    t.import_file(str(ROOT / "examples" / "comments_example.csv"), "facebook", "2026-11-15")
    con = sqlite3.connect("tracker.db")
    rows = con.execute("SELECT title, speaker FROM items").fetchall()
    assert len(rows) == 6
    assert all(title == "" and speaker.startswith("Commenter") for title, speaker in rows)
    t.import_file(str(ROOT / "examples" / "comments_example.csv"), "facebook", "2026-11-15")
    assert count() == 6                 # re-import adds nothing


def test_import_txt_skips_short_paragraphs(t):
    t.import_file(str(ROOT / "examples" / "minutes_example.txt"), "minutes", "2026-11-12")
    assert count("minutes") == 5        # the "#" note line is ignored


def test_import_bad_date(t):
    with pytest.raises(SystemExit):
        t.import_file(str(ROOT / "examples" / "minutes_example.txt"), "minutes", "12/11/2026")


# ---------------------------------------------------------------- statistics
def test_weights_and_shares(t):
    t.collect()
    t.import_file(str(ROOT / "examples" / "comments_example.csv"), "facebook", "2026-11-15")
    items, _ = t.load()
    stats = t.weekly_stats(items)
    for s in stats.values():
        assert abs(sum(s["shares"].values()) - 1) < 1e-9
        if s["n"] < t.THIN:
            assert s["ci"] is None
        else:
            assert s["ci"][0] <= s["mean"] <= s["ci"][1]
    # a weight-2 source counts twice a weight-1 source
    assert t.weight_of("minutes") == 2 * t.weight_of("news")


def test_bootstrap_is_repeatable(t):
    pairs = [(0.5, 1), (-0.2, 2), (0.1, 1), (0.4, 1)]
    assert t.bootstrap_ci(pairs, 42) == t.bootstrap_ci(pairs, 42)


# ---------------------------------------------------------------- outputs
def test_outputs_build(t):
    t.collect()
    t.import_file(str(ROOT / "examples" / "minutes_example.txt"), "minutes", "2026-11-12")
    t.report()
    t.summary()
    t.dashboard(open_it=False)
    for f in ("weekly_report.csv", "evidence.csv", "weekly_summary.md", "dashboard.html"):
        assert Path(f).stat().st_size > 0
    html = Path("dashboard.html").read_text()
    data = json.loads(html.split('type="application/json">')[1].split("</script>")[0].replace("<\\/", "</"))
    assert len(data["items"]) == count()
    assert "__DATA__" not in html


# ---------------------------------------------------------------- v0.4 additions
@pytest.mark.parametrize("text", [
    "The developer announced the campus on Wednesday.",
    "The official told the paper it would take a decade.",
    "The site has plenty of space for six turbines.",
    "Bill Smith attended the session.",
    "Stay tuned for updates.",
])
def test_no_theme_false_positives(t, text):
    assert t.themes_of(text) == []


@pytest.mark.parametrize("text,theme", [
    ("Residents said they were not told about the plan.", "transparency_trust"),
    ("The jobs would let our kids stay home.", "jobs_economy"),
    ("Electric bills will go up.", "cost_finance"),
    ("Is there an evacuation plan?", "safety"),
    ("What happens to the waste?", "environment"),
    ("Flooding on the river is bad enough.", "environment"),
])
def test_theme_matches(t, text, theme):
    assert theme in t.themes_of(text)


def test_news_outlet_kept_but_not_scored(t):
    t.collect()
    con = sqlite3.connect("tracker.db")
    rows = con.execute("SELECT title, speaker FROM items WHERE source='news'").fetchall()
    assert rows and all(" - " not in title and speaker for title, speaker in rows)


def test_rescore_applies_new_settings(t):
    t.import_file(str(ROOT / "examples" / "comments_example.csv"), "facebook", "2026-11-15")
    before = sqlite3.connect("tracker.db").execute("SELECT COUNT(*) FROM theme_scores").fetchone()[0]
    assert before > 0
    t.THEME_RE.clear()                  # pretend every theme was removed
    t.rescore()
    con = sqlite3.connect("tracker.db")
    assert con.execute("SELECT COUNT(*) FROM theme_scores").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 6


def test_validate_roundtrip(t, capsys):
    t.import_file(str(ROOT / "examples" / "comments_example.csv"), "facebook", "2026-11-15")
    t.export_labels(6, "labels.csv")
    import csv
    rows = list(csv.DictReader(open("labels.csv")))
    items = {i["id"]: i for i in t.load()[0]}
    for r in rows:                       # label everything exactly as the model does
        r["your_label"] = t.label(items[r["item_id"]]["overall"])
    with open("labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    capsys.readouterr()
    t.validate("labels.csv")
    assert "6 of 6 (100%)" in capsys.readouterr().out
    assert t.norm_label("neutral") == "neutral" and t.norm_label("n") == "negative"
    assert t.norm_label("P") == "positive" and t.norm_label("") is None


def test_weeks_are_continuous_and_periods_exist(t):
    t.collect()
    items, _ = t.load()
    weeks = t.all_weeks(items)
    assert "2026-W44" in weeks and "2026-W45" in weeks      # no gaps
    assert weeks == sorted(weeks)
    ps = t.period_stats(items, weeks)
    assert ("ALLTIME", "ALL") in ps and ps[("ALLTIME", "ALL")]["n"] == len(items)


# ---------------------------------------------------------------- project settings
def test_defaults_derived_from_name(t):
    t.configure({"project_name": "Hill Road Bypass", "area_name": "Ashby",
                 "relevance": {"topic_terms": ["bypass"]}})
    assert t.NEWS_QUERIES[0] == '"Hill Road Bypass"'
    assert t.MASTODON_TAGS == ["HillRoadBypass"]
    assert t.is_relevant("Ashby bypass plans criticised")
    assert not t.is_relevant("Ashby school fete")


def test_theme_settings(t):
    t.configure({"project_name": "X", "themes": {"traffic_amenity": None, "heritage": ["listed building"]},
                 "theme_additions": {"safety": ["landslip"]}, "source_weights": {"news": 0.5},
                 "lexicon": {"Eyesore": -3}})
    assert "traffic_amenity" not in t.THEMES
    assert t.themes_of("The listed building is at risk.") == ["heritage"]
    assert "safety" in t.themes_of("Fears of a landslip on the hill.")
    assert t.weight_of("news") == 0.5 and t.weight_of("minutes") == 2.0
    assert t.DOMAIN_LEXICON["eyesore"] == -3


def test_bad_theme_pattern_is_reported(t):
    with pytest.raises(SystemExit, match="not a valid regular expression"):
        t.configure({"project_name": "X", "theme_additions": {"safety": ["(unclosed"]}})


def test_init_writes_usable_settings(t):
    t.init_project("Riverton Energy Park", "Marlow County", ["Riverton"], ["energy"])
    cfg = t.load_config()
    assert cfg["project_name"] == "Riverton Energy Park"
    assert cfg["relevance"]["place_terms"] == ["marlow county", "riverton"]
    t.configure(cfg)
    assert t.is_relevant("Riverton energy scheme debated")
    with pytest.raises(SystemExit, match="already exists"):
        t.init_project("Other", "", [], [])


# ---------------------------------------------------------------- setup questions
def answer_with(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_wizard_builds_settings(t, monkeypatch, capsys):
    answer_with(monkeypatch, ["", "Riverton Energy Park", "Marlow County", "Riverton, Mill Lane",
                              "1", "generating station", "bank, church", "2026-10", "n", ""])
    t.init_wizard()
    cfg = t.load_config()
    assert cfg["project_type"] == "nuclear"
    assert cfg["relevance"]["place_terms"] == ["marlow county", "riverton", "mill lane"]
    assert cfg["relevance"]["topic_terms"][0] == "generating station"
    assert "nuclear" in cfg["relevance"]["topic_terms"]
    assert cfg["relevance"]["exclude_terms"] == ["riverton energy park bank", "riverton energy park church"]
    t.configure(cfg)
    assert "safety" in t.themes_of("Fears about radioactive leaks.")
    assert t.is_relevant("Mill Lane reactor plan debated")
    assert not t.is_relevant("Riverton Energy Park Bank opens")
    assert "collect --preview" in capsys.readouterr().out
    assert cfg["filters"]["languages"] == ["en"] and cfg["filters"]["max_hashtags"] == 3


def test_wizard_all_defaults(t, monkeypatch):
    answer_with(monkeypatch, ["Hill Road Bypass", "", "", "", "", "", "", "", "n"])
    t.init_wizard()
    cfg = t.load_config()
    assert cfg["project_type"] == "other" and cfg["theme_additions"] == {}
    assert cfg["filters"] == {}                       # answered "n" to the filters question
    t.configure(cfg)
    assert t.is_relevant("Hill Road Bypass approved")


def test_every_project_type_is_valid(t, tmp_path):
    for kind in t.PROJECT_TYPES:
        path = str(tmp_path / f"{kind}.json")
        t.init_project("Test Scheme", "Ashby", [], [], path, kind)
        t.configure(t.load_config(path))        # raises if a pattern is broken


# ---------------------------------------------------------------- relevance precision
def test_name_in_ordinary_sentence_is_not_enough(t):
    t.configure({"project_name": "Golden Future", "area_name": "Ashby",
                 "relevance": {"topic_terms": ["quarry"]}})
    assert t.is_relevant("Golden Future wins approval")                 # exact name
    assert not t.is_relevant("we all hope for a golden future someday")  # ordinary words
    assert t.is_relevant("the golden future quarry is a bad idea")       # words + topic
    assert t.is_relevant("Ashby quarry plan criticised")                 # place + topic


def test_strict_needs_context_even_for_exact_name(t):
    t.configure({"project_name": "Golden Future", "area_name": "Ashby",
                 "relevance": {"topic_terms": ["quarry"], "strict": True}})
    assert not t.is_relevant("Golden Future is what we wish you")
    assert t.is_relevant("Golden Future quarry opposed")


def test_start_date_and_prune(t, capsys):
    t.collect()
    total = count()
    assert total > 0
    cfg = dict(TEST_PROJECT, start_date="2026-11")
    t.configure(cfg)
    assert t.START_DATE == "2026-11-01"
    assert 0 < len(t.load()[0]) < total          # older items hidden straight away
    t.prune()                                    # dry run removes nothing
    assert count() == total
    t.prune(apply=True)
    assert count() == len(t.load()[0])
    t.import_file(str(ROOT / "examples" / "minutes_example.txt"), "minutes", "2026-11-12")
    before = count()
    t.configure(dict(TEST_PROJECT, relevance={"project_terms": ["nothing matches this"]}))
    t.prune(apply=True)
    assert count("minutes") == 5 and count() < before   # imports are never pruned
    with pytest.raises(SystemExit):
        t.configure(dict(TEST_PROJECT, start_date="October"))


# ---------------------------------------------------------------- safeguards
def test_preview_stores_nothing(t, capsys):
    t.collect(preview=True)
    out = capsys.readouterr().out
    assert "Nothing has been stored" in out
    assert "WOULD KEEP" in out and "WOULD REJECT" in out
    assert not os.path.exists("tracker.db")


def test_reasons(t):
    assert t.relevance_reason("Riverton Energy Park approved") == "name+context"   # "energy" is a topic word
    t.configure({"project_name": "Golden Future", "area_name": "Ashby",
                 "relevance": {"topic_terms": ["quarry"]}})
    assert t.relevance_reason("Golden Future approved") == "name"
    assert t.relevance_reason("golden future quarry row") == "name+context"
    assert t.relevance_reason("Ashby quarry row") == "place+topic"
    assert t.relevance_reason("a golden future awaits") is None


def loose_items(t, n_old=12, n_new=14):
    con = t.db()
    for k in range(n_old):      # old, unrelated: name only, months before
        t.save_item(con, "bluesky", f"u://old{k}", "", f"Golden Future is my wish for you all {k}",
                    f"2026-0{1 + k % 6}-1{k % 9}T10:00:00Z")
    for k in range(n_new):
        t.save_item(con, "bluesky", f"u://new{k}", "", f"Golden Future quarry plan is bad news {k}",
                    f"2026-11-{10 + k % 9}T10:00:00Z")
    con.commit()
    con.close()


def test_checks_flag_loose_settings(t):
    t.configure({"project_name": "Golden Future", "area_name": "Ashby",
                 "relevance": {"topic_terms": ["quarry"]}})
    loose_items(t)
    found = " ".join(t.quality_checks(t.load(all_dates=True)[0]))
    assert "no start_date" in found and '"start_date"' in found
    assert "One source" in found
    # tightening the settings clears the first warning and points at prune
    t.configure({"project_name": "Golden Future", "area_name": "Ashby", "start_date": "2026-10",
                 "relevance": {"topic_terms": ["quarry"]}})
    found = " ".join(t.quality_checks(t.load(all_dates=True)[0]))
    assert "no start_date" not in found and "older than your start_date" in found
    t.prune(apply=True)
    found = " ".join(t.quality_checks(t.load(all_dates=True)[0]))
    assert "older than" not in found and "no longer fit" not in found


def test_check_flags_name_only_majority(t):
    t.configure({"project_name": "Golden Future", "area_name": "Ashby", "start_date": "2026-01",
                 "relevance": {"topic_terms": ["quarry"]}})
    loose_items(t, n_old=12, n_new=4)
    found = " ".join(t.quality_checks(t.load(all_dates=True)[0]))
    assert "name alone" in found
    assert all(i["kept"] in ("name", "name+context") for i in t.load()[0])


def test_project_words_are_neutral(t):
    t.configure({"project_name": "Prosperity Gardens", "area_name": "Ashby",
                 "relevance": {"topic_terms": ["energy"]}})
    assert t.scorer()("Prosperity Gardens energy scheme discussed") == 0
    assert t.scorer()("Prosperity Gardens is a wonderful idea") > 0.3
    t.configure({"project_name": "Prosperity Gardens", "lexicon": {"prosperity": 2}})
    assert t.scorer()("Prosperity Gardens discussed") > 0      # an explicit setting wins


# ---------------------------------------------------------------- user filters
POSTS = [  # (text, language reported by the source, date)
    ("Golden Future quarry will wreck the valley and nobody asked us.", "en", "2026-11-10"),
    ("Company Unveils Golden Future: A Major Quarry in Ashby #Ashby #Quarry #Mining #News", "en", "2026-11-10"),
    ("Perusahaan mengumumkan Golden Future, sebuah tambang quarry besar di Ashby", "id", "2026-11-11"),
    ("Golden Future quarry plan gets county backing after long debate tonight", "en", "2026-11-12"),
    ("Golden Future quarry plan gets county backing after a long debate", "en", "2026-11-13"),
    ("Golden Future quarry? Sponsored: buy gold now", "en", "2026-11-14"),
    ("Golden Future quarry", "en", "2026-11-15"),
]
QUARRY = {"project_name": "Golden Future", "area_name": "Ashby",
          "relevance": {"topic_terms": ["quarry"]}}


def add_posts(t):
    con = t.db()
    for k, (text, lang, day) in enumerate(POSTS):
        t.save_item(con, "bluesky", f"u://p{k}", "", text, day + "T10:00:00Z", lang=lang)
    con.commit()
    con.close()


def test_no_filters_by_default(t):
    t.configure(dict(QUARRY))
    add_posts(t)
    assert len(t.load()[0]) == len(POSTS)


def test_each_filter(t):
    add_posts_cfg = lambda f: t.configure(dict(QUARRY, filters=f))
    add_posts_cfg({})
    add_posts(t)
    hidden = lambda: sorted(i["hidden"] for i in t.apply_filters(t.load(all_dates=True)[0])[1])
    add_posts_cfg({"languages": ["en"]})
    assert hidden() == ["language"]
    add_posts_cfg({"max_hashtags": 3})
    assert hidden() == ["hashtags"]
    add_posts_cfg({"collapse_reposts": True})
    assert hidden() == ["repost"]
    add_posts_cfg({"min_words": 4})
    assert hidden() == ["short"]
    add_posts_cfg({"block_phrases": ["Sponsored"]})
    assert hidden() == ["phrase"]
    add_posts_cfg({"skip_sources": ["bluesky"]})
    assert len(hidden()) == len(POSTS)
    add_posts_cfg(dict(t.RECOMMENDED_FILTERS))
    assert len(t.load()[0]) == 3                      # 2 genuine posts + the sponsored one
    assert count() == len(POSTS)                      # hidden, not deleted
    found = " ".join(t.quality_checks(t.load(all_dates=True)[0]))
    assert "filters are hiding 4 of 7" in found
    t.prune(apply=True)
    assert count() == 3


def test_filters_never_touch_imports(t):
    t.configure(dict(QUARRY, filters={"min_words": 50, "languages": ["en"], "max_hashtags": 0}))
    t.import_file(str(ROOT / "examples" / "comments_example.csv"), "facebook", "2026-11-15")
    assert len(t.load()[0]) == 6


def test_unknown_filter_is_reported(t):
    with pytest.raises(SystemExit, match="Unknown filter"):
        t.configure(dict(QUARRY, filters={"english": True}))


def test_script_guess_when_language_unknown(t):
    t.configure(dict(QUARRY, filters={"languages": ["en"]}))
    assert t.static_filter("bluesky", "Golden Future карьер это плохая идея для всех нас", "") == "language"
    assert t.static_filter("bluesky", "Golden Future quarry is a bad idea", "") is None


def test_collect_applies_filters_and_stores_language(t, capsys):
    t.configure(dict(TEST_PROJECT, filters={"skip_sources": ["mastodon"], "min_words": 4}))
    t.collect()
    out = capsys.readouterr().out
    assert "[mastodon] switched off" in out and count("mastodon") == 0
    con = sqlite3.connect("tracker.db")
    assert con.execute("SELECT COUNT(*) FROM items WHERE source='news' AND lang='en'").fetchone()[0] == 3


def test_older_database_gains_language_column(t):
    con = t.db()
    con.execute("ALTER TABLE items DROP COLUMN lang")
    con.commit()
    con.close()
    t.db().close()
    cols = [r[1] for r in sqlite3.connect("tracker.db").execute("PRAGMA table_info(items)")]
    assert "lang" in cols


def test_busy_gdelt_is_not_retried_for_tone(t, monkeypatch, capsys):
    calls = {"n": 0}

    def busy(url, **kw):
        if "gdelt" in url:
            calls["n"] += 1
            return fakes.FakeResponse("busy", 429)
        return fakes.fake_get(url, **kw)
    monkeypatch.setattr(t.requests, "get", busy)
    t.collect()
    out = capsys.readouterr().out
    assert calls["n"] == 4                      # one request, three retries, no tone attempt
    assert "GDELT is limiting requests" in out and "skipped along with gdelt" in out
    assert count("news") > 0                    # other sources still collected


# ---------------------------------------------------------------- found on real data
def test_neutral_words_stay_neutral_in_capitals_and_after_intensifiers(t):
    t.configure({"project_name": "Golden Future", "area_name": "Ashby County",
                 "relevance": {"topic_terms": ["energy", "power plant"]}})
    sc = t.scorer()
    assert sc("ASHBY COUNTY, Oct. 21 -- The company announced a plan.") == 0
    assert sc("Enormous energy project proposed in Ashby County") == 0
    assert sc("State energy planner weighs in on the SAFETY review") == 0


def test_comparison_like_is_not_praise(t):
    sc = t.scorer()
    assert sc("Developers pushing projects like this one") == 0
    assert sc("I like this plan") > 0 and sc("People really like it") > 0


def test_criticism_of_private_gain_is_negative(t):
    sc = t.scorer()
    assert sc("Developers risk socializing construction costs while privatizing energy profits.") < -0.3


def test_hype_emoji_do_not_score(t):
    assert t.scorer()("\U0001F4A5 The county is set to host a new plant") == 0


def test_site_description_is_not_an_environment_comment(t):
    assert "environment" not in t.themes_of("Six reactors planned along the Marlow River.")
    assert "environment" in t.themes_of("Worried about water quality and wildlife.")


def test_headline_shares_are_collapsed(t):
    t.configure(dict(QUARRY, filters={"collapse_reposts": True}))
    con = t.db()
    t.save_item(con, "news", "u://n1", "Ashby quarry plan wins county approval after long debate", "",
                "2026-11-10T09:00:00Z", speaker="Example Gazette")
    t.save_item(con, "bluesky", "u://b1", "", "Ashby quarry plan wins county approval after long debate "
                "https://news.example/x", "2026-11-10T12:00:00Z")
    t.save_item(con, "bluesky", "u://b2", "", "Ashby quarry plan wins county approval after long debate. "
                "This is a disgrace, the council ignored every single objection we raised.",
                "2026-11-11T12:00:00Z")
    con.commit()
    con.close()
    shown = {i["url"] for i in t.load()[0]}
    assert shown == {"u://n1", "u://b2"}      # bare share hidden; share with a comment kept


def test_note_when_no_local_records(t):
    t.configure(dict(QUARRY))
    loose_items(t, n_old=0, n_new=12)
    assert "no local records" in " ".join(t.quality_checks(t.load(all_dates=True)[0]))


def test_spaced_out_gdelt_titles_match_news(t):
    assert t.norm_key("Park Announced , a 6 . 6 Gigawatt Campus in Marlow County", "") == \
        t.norm_key("Park Announced, a 6.6 Gigawatt Campus in Marlow County - Example Gazette", "")


def test_init_next_steps_name_a_custom_settings_file(t, capsys):
    t.init_project("Hill Road Bypass", path="other.json")
    assert "--project other.json collect --preview" in capsys.readouterr().out
