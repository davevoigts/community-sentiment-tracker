# Contributing

Thanks for your interest. Bug reports, new sources, scoring improvements and adaptations for other projects are all welcome.

## Getting set up

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Ground rules

- **Tests stay offline.** Add fake replies to `tests/fakes.py` rather than calling live services.
- **Example data stays fictional.** No real outlets, headlines, people or comments in tests or examples.
- **Keep the evidence.** A change to scoring or weighting should keep every number traceable in the dashboard, and should be described in `docs/METHOD.md`.
- **Respect platform terms.** New sources should use open feeds or official APIs only.
- **Python 3.9 compatible.** CI runs on 3.9, 3.12 and 3.13.

## Adding a source

Write a function returning `(source, url, title, text, published_iso)` tuples, add it to `SOURCES`, give it a weight in `DEFAULT_SOURCE_WEIGHTS`, document it in `docs/SOURCES.md`, and add a test with fake replies.

## Pull requests

Keep them focused, describe what changed and why, and note any change to how scores or weights are calculated.
