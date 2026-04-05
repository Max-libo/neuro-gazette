# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Нейрогазета** — an automated daily AI-news digest. A GitHub Actions workflow runs every day at 07:00 МСК (04:00 UTC), calls the Anthropic API with `web_search` tool to collect AI news for the last 24 hours, and commits the result as JSON to `docs/data/`. GitHub Pages serves the static frontend from `docs/`.

## Running locally

```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-... python scripts/collect.py
# then open docs/index.html in a browser
```

The script writes `docs/data/YYYY-MM-DD.json`, `docs/data/latest.json`, and updates `docs/data/index.json`.

## Architecture

### Data pipeline (`scripts/collect.py`)

Three-stage pipeline:

1. **Сбор (RSS + scrape)** — parallel HTTP fetch from `sources.yaml` (types `rss` / `scrape`). Each item gets a `priority` (1-3) from its source config. Results are deduplicated and sorted by priority before the `RAW_LIMIT` (200) cutoff.
2. **Веб-поиск** — `fetch_via_search()` calls `claude-sonnet-4-6` with `web_search_20250305` tool (`max_uses=2`) for each query in `search_queries` (currently `models` and `hype` sections).
3. **Фильтрация + редактура** — two Claude API calls:
   - `filter_with_claude()` — selects 30-40 most relevant articles from the raw list
   - `edit_with_claude()` — formats the final issue as JSON with `EDIT_SYSTEM` prompt

- `_api_call()` retries up to 3 times on rate limits (reads `retry-after` header) and API errors
- `edit_with_claude()` retries up to 3 times on JSON parse errors
- `validate_and_fix()` normalises each news item and deduplicates IDs before saving
- Raw articles are cached to `docs/data/YYYY-MM-DD_raw.json` before Claude calls

### JSON schema for a news item
```json
{
  "id": "section-md5hash-YYYY-MM-DD",
  "section": "models|platforms|industry|hype",
  "headline": "...",
  "subheadline": "...",
  "body": "...",
  "importance": 1-10,
  "sources": [{"title": "...", "url": "...", "type": "official|media|rumor"}],
  "unconfirmed": false,
  "duplicate_note": null,
  "tags": {"entities": [], "sentiment": "positive|negative|neutral|rumor", "event": "release|update|shutdown|investment|regulation|leak"}
}
```

### Frontend (`docs/`)
- Pure static HTML/CSS/JS, no build step
- `app.js` renders JSON data; items with `importance` 9-10 → hero layout, 7-8 → large, ≤6 → compact
- `editor/index.html` — password-protected editor (SHA-256 hash stored in `PASSWORD_HASH` variable; default password: `neuro2026`)
- `archive/index.html` — lists all issues from `data/index.json`

### CI (`daily.yml`)
- Runs `collect.py`, commits changes to `docs/data/`, pushes, optionally notifies Telegram
- Required secret: `ANTHROPIC_API_KEY`
- Optional secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

## Changing the editor password

```bash
echo -n "new_password" | sha256sum
# update PASSWORD_HASH in docs/editor/index.html
```
