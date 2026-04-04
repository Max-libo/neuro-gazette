# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Нейрогазета** — an automated daily AI-news digest. A GitHub Actions workflow runs every day at 07:00 МСК (04:00 UTC), calls the Anthropic API with `web_search` tool to collect AI news for the last 24 hours, and commits the result as JSON to `docs/data/`. GitHub Pages serves the static frontend from `docs/`.

## Running locally

```bash
pip install anthropic
ANTHROPIC_API_KEY=sk-... python scripts/collect.py
# then open docs/index.html in a browser
```

The script writes `docs/data/YYYY-MM-DD.json`, `docs/data/latest.json`, and updates `docs/data/index.json`.

## Architecture

### Data pipeline (`scripts/collect.py`)
- Single async call to `claude-sonnet-4-6` with `web_search_20250305` tool (`max_uses=8`)
- Collects all 4 sections (`models`, `platforms`, `industry`, `hype`) in one request using `SYSTEM_PROMPT` (cached) + `make_user_prompt()`
- Retries up to 5 times on JSON parse errors, rate limits (reads `retry-after` header), or API errors
- `validate_and_fix()` normalises each news item and deduplicates IDs before saving

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
