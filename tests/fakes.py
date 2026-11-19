"""Fictional data and fake network replies, so tests run offline.
Every outlet, headline, post and person here is invented."""
import json
import requests


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, str) else json.dumps(body)
        self.status_code = status

    @property
    def content(self):
        return self.body.encode()

    @property
    def text(self):
        return self.body

    def json(self):
        return json.loads(self.body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


NEWS = [
    ("Startup proposes massive energy campus in Marlow County - Example Gazette", "Wed, 21 Oct 2026 17:00:00 GMT"),
    ("Marlow County residents question energy plan at public meeting - Sample News 7", "Fri, 30 Oct 2026 12:00:00 GMT"),
    ("Riverton Energy Park backers say energy jobs will revive downtown - Test Herald", "Tue, 03 Nov 2026 12:00:00 GMT"),
    ("Marlow County energy hearing draws packed, angry crowd - Sample News 7", "Thu, 12 Nov 2026 22:00:00 GMT"),
    ("Local team wins regional final - Example Gazette", "Mon, 16 Nov 2026 12:00:00 GMT"),
]
GDELT_ARTICLES = [
    {"url": "https://news.example/a1", "title": "Startup proposes massive energy campus in Marlow County",
     "seendate": "20261021T170000Z"},
    {"url": "https://news.example/a2", "title": "Marlow County energy plan faces safety questions",
     "seendate": "20261117T120000Z"},
]
PAGES = {
    "a1": "<html><body><article><p>A developer announced Riverton Energy Park, a large generating campus in Marlow County.</p>"
          "<p>A county official said the jobs would let young people stay and would revive an empty downtown.</p>"
          "<p>Some residents said they were not told about the plan until the announcement, and felt the process was rushed.</p>"
          "</article></body></html>",
    "a2": "<html><body><article><p>Questions about plant safety continue to follow Riverton Energy Park in Marlow County.</p>"
          "<p>Residents near the river fear an accident would be devastating, while engineers say the design is among the safest in the world.</p>"
          "<p>Environmental groups warned about the effect on wildlife and water quality in the Marlow River.</p>"
          "</article></body></html>",
}
TONE = {"timeline": [{"series": "Average Tone", "data": [
    {"date": "20261021T000000Z", "value": 1.2}, {"date": "20261030T000000Z", "value": -1.6},
    {"date": "20261105T000000Z", "value": -1.9}, {"date": "20261112T000000Z", "value": -2.4},
    {"date": "20261118T000000Z", "value": -1.1}]}]}
MASTODON = [
    {"url": "https://social.example/@a/1", "content": "<p>Riverton Energy Park in Marlow County is exactly the clean, reliable energy this region needs.</p>",
     "created_at": "2026-10-22T09:00:00.000Z"},
    {"url": "https://social.example/@c/3", "content": "<p>Lovely autumn walk today #nature</p>",
     "created_at": "2026-11-07T09:00:00.000Z"},
]
BLUESKY = {"posts": [
    {"uri": f"at://did:plc:x/app.bsky.feed.post/p{i}", "author": {"handle": f"user{i}.example"},
     "record": {"text": text, "createdAt": date}}
    for i, (text, date) in enumerate([
        ("Proud of Marlow County for going after Riverton Energy Park. Good jobs, finally.", "2026-10-23T14:00:00.123Z"),
        ("Nobody in town was consulted before the Riverton Energy Park announcement. Feels secretive.", "2026-10-31T14:00:00Z"),
        ("Honestly excited about Riverton Energy Park. My kids could actually stay in the county.", "2026-11-17T18:00:00.5Z"),
    ])]}


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://news.google.com/rss/articles/{i}</link>"
                   f"<description>d</description><pubDate>{d}</pubDate></item>" for i, (t, d) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>"


def fake_get(url, **kw):
    params = kw.get("params", {})
    if "gdelt" in url:
        return FakeResponse(TONE if params.get("mode") == "TimelineTone" else {"articles": GDELT_ARTICLES})
    if "news.google" in url:
        return FakeResponse(rss(NEWS))
    if "/timelines/tag/" in url:
        return FakeResponse(MASTODON if "RivertonEnergyPark" in url else [])
    if "searchPosts" in url:
        return FakeResponse(BLUESKY if params.get("q") == '"Riverton Energy Park"' else {"posts": []})
    for key, page in PAGES.items():
        if url.endswith(key):
            return FakeResponse(page)
    raise AssertionError("unexpected request: " + url)


def fake_post(url, **kw):
    return FakeResponse({"accessJwt": "test-token"})
