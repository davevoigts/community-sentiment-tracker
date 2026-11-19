# Sources

| Source | Endpoint | Login | What is stored | Notes |
|---|---|---|---|---|
| Google News | `news.google.com/rss/search` | None | Headline, link, date | Description only repeats the headline; links are encoded redirects, so article text can't be fetched. |
| GDELT articles | `api.gdeltproject.org/api/v2/doc/doc` (ArtList) | None | Headline, link, date, article text where extractable | English-language sources only. About 1 request per 5 seconds; 429 responses are retried after 15, 30 and 60 seconds. |
| GDELT tone | same, `TimelineTone` mode | None | Daily average tone | Own scale; displayed separately. |
| Mastodon | `/api/v1/timelines/tag/{tag}` | None | Post text, link, date | One instance only; federation means coverage is partial. |
| Bluesky | `bsky.social/xrpc/app.bsky.feed.searchPosts` | App password | Post text, link, date | Unauthenticated search returns 403. |
| Imports | local `.txt` / `.csv` | n/a | Text, speaker (not scored), date | Use for minutes, transcripts, surveys and hand-copied comments. |

## Not collected automatically

- **Reddit.** New Data API access is limited to moderation use cases under Reddit's Responsible Builder Policy, and the public JSON endpoints block unauthenticated requests. Import comments by hand.
- **Facebook.** Scraping breaks Facebook's terms. Local groups are often where the most relevant discussion happens, so consider copying representative comments, anonymised, into a CSV.
- **X (Twitter).** The API is paid.

## Adding a source

Write a function returning a list of `(source, url, title, text, published_iso)` tuples, add it to `SOURCES`, and give it a weight in `DEFAULT_SOURCE_WEIGHTS`. Relevance filtering, deduplication, scoring and the dashboard then apply automatically.
