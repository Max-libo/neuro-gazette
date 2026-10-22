# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Нейрогазета** — an automated daily AI-news digest. Each morning at 07:00 МСК a 4-stage pipeline runs, collects AI news, calls Claude to filter and edit it into a structured JSON issue, and publishes the result. The static frontend in `docs/` is deployed to REG.RU via FTP on every push to `main`.

## Running the pipeline locally

**Primary method — Claude CLI subscription (no API key needed):**
```bash
pip install -r requirements.txt
./run.sh              # full run: stages 1-4 + git push
./run.sh --no-push    # run without committing
./run.sh --from-raw   # skip stages 1-2, reuse cached raw+search data
./run.sh --from-filter  # skip stages 1-3, reuse filtered list
```

**Legacy single-script method — requires ANTHROPIC_API_KEY:**
```bash
ANTHROPIC_API_KEY=sk-... python scripts/collect.py
```

After either method, open `docs/index.html` in a browser to see the result.

## Pipeline architecture

`run.sh` orchestrates four Python scripts in `scripts/pipeline/`:

| Stage | Script | Input | Output | Model |
|-------|--------|-------|--------|-------|
| 1 | `stage1_collect.py` | `sources.yaml` | `{date}_raw.json` | — |
| 2 | `stage2_search.py` | `sources.yaml` search_queries | `{date}_search.json` | Sonnet (WebSearch tool via CLI) |
| 3 | `stage3_filter.py` | `_raw.json` + `_search.json` | `{date}_filtered.txt` | Sonnet (via CLI) |
| 4 | `stage4_edit.py` | `_filtered.txt` + `_search.json` | `{date}.json`, `latest.json` | Opus (via CLI) |

After stage 4, `generate_preview.py` builds OG-preview HTML in `docs/preview/`, then `run.sh` commits `docs/data/` and `docs/preview/` and optionally notifies Telegram.

**Shared code:** `scripts/pipeline/common.py` — time window calculation, URL validation, date parsing, `validate_and_fix()`, `finalize_news()`, `run_claude()` (wraps `claude --print`), changelog versioning.

**Справочник сущностей:** `entities.yaml` (корень репо) + `scripts/pipeline/entities.py`. `validate_and_fix()` приводит `tags.entities` и `related[].entities` к каноническим именам (регистр, дефисы, точки, «ё» не важны; варианты — в `aliases`), выкидывает темы из `stoplist`, неизвестные сущности пропускает как есть и пишет их списком в лог этапа 4. Этап 4 получает список канонических имён в системном промпте. Каноническое имя: компании, продукты, модели, люди — в оригинальном написании; страны и госорганы — по-русски. Поле `parent` (продукт → компания) пока используется только справочником, для статистики — следующий шаг.
```bash
python scripts/pipeline/entities.py --check    # проверить справочник
python scripts/pipeline/entities.py --report   # что склеится в истории и чего нет в справочнике
```

**`run_claude()`** calls `claude --print --model <model>` via subprocess, reads stdin. Rate-limit handling: waits 60 min and retries up to 6 times.

### Stage 1: collect

Parallel HTTP fetch (ThreadPoolExecutor, 10 workers) from all `sources` in `sources.yaml`. Source types:
- `rss` — parsed with feedparser
- `scrape` — HTML parsed with BeautifulSoup; tries JSON-LD structured data first, falls back to `h1/h2/h3` scan

Items filtered by 24h window, deduplicated by URL and title. Results sorted by `priority` (1=highest), truncated to `RAW_LIMIT=200`.

### Stage 2: web search

For each query in `search_queries` (by section), calls `claude --print --allowedTools WebSearch`. Parses `TITLE | URL | DATE | description` lines, validates URLs (`is_vague_url()`), drops results outside the time window.

### Stage 3: filter

Single Claude call with the raw article list and search results. Returns filtered `title | URL | source | date` lines as `{date}_filtered.txt`. Checks 7 days of history to flag duplicate headlines.

### Stage 4: edit (Opus)

Calls Opus with `EDIT_SYSTEM` prompt (defined in `stage4_edit.py`). Retries up to 3 times on JSON parse errors. Output validated by `finalize_news()`:
- `validate_and_fix()` normalises each item, deduplicates IDs, maps `tier` → `importance` (hero=9, regular=7, compact=5)
- `finalize_news()` enforces exactly one `"tier": "hero"` per issue
- Appends a `changelog` card (auto-version-bumped when code files change)

### JSON schema for a news item
```json
{
  "id": "section-md5hash-YYYY-MM-DD",
  "section": "models|platforms|industry|hype",
  "headline": "...",
  "subheadline": "...",
  "body": "...",
  "tier": "hero|regular|compact",
  "importance": 9,
  "sources": [{"title": "...", "url": "...", "type": "official|media|rumor"}],
  "related": [{"title": "...", "url": "...", "entities": [], "sentiment": "positive|negative|neutral"}],
  "unconfirmed": false,
  "duplicate_note": null,
  "tags": {
    "entities": ["OpenAI", "xAI"],
    "sentiment": "positive|negative|neutral",
    "entity_sentiment": {"OpenAI": "neutral", "xAI": "negative"},
    "event": "release|update|..."
  }
}
```

`tags.sentiment` — общий тон новости, `tags.entity_sentiment` — тональность для каждой сущности из `tags.entities` (как новость сказывается именно на ней); ключи всегда совпадают со списком `entities`, пропуски дозаполняются общим тоном. Слух — не тональность: `"rumor"` превращается в `unconfirmed: true` + `neutral`. Типы событий (`tags.event`) заданы словарём `EVENTS` в `common.py` — из него собираются валидация и описание для промпта; метки для сайта — `EVENT_LABELS` в `docs/analytics/index.html`. Неизвестное значение → `other` с записью в лог (раньше молча становилось `update`). В выпусках до октября 2026 карты нет — `stats.py` и аналитика берут общий тон.

## Frontend (`docs/`)

Pure static HTML/CSS/JS, no build step.

- `app.js` — fetches `data/latest.json`, renders the issue. Layout by tier: `hero` → hero card, `regular` → large card, `compact` → compact card. `changelog` tier → pinned footer card.
- `archive/index.html` — lists all issues from `data/index.json`
- `editor/index.html` — password-protected in-browser editor. `PASSWORD_HASH` = SHA-256 of password. Default password: `neuro2026`. Change: `echo -n "new_password" | sha256sum`
- `analytics/index.html` — entity/sentiment stats from `data/stats.json` (generated by `scripts/stats.py`)
- `preview/` — OG-image HTML files (one per issue), generated by `scripts/generate_preview.py`

## CI / Deployment

`.github/workflows/deploy.yml` — on push to `main`, deploys `docs/` to REG.RU via FTP (excludes `editor/`). Required secrets: `FTP_SERVER`, `FTP_USERNAME`, `FTP_PASSWORD`.

`.github/workflows/daily.yml` — ежедневный сбор выпуска в GitHub Actions (не зависит от мака/VPS). Запуски в 07:07 / 10:07 / 14:07 МСК (10:07 и 14:07 — self-heal). Ставит Claude Code CLI, авторизуется через секрет `CLAUDE_CODE_OAUTH_TOKEN` (подписка, получить: `claude setup-token`), запускает `morning.sh`, затем явно вызывает `deploy.yml` (пуш от `GITHUB_TOKEN` сам деплой не триггерит). Кэш этапов 1–3 (`*_raw.json`, `*_search.json`, `*_filtered.txt`) переносится между запусками одного дня через `actions/cache`. Секреты: `CLAUDE_CODE_OAUTH_TOKEN`, `TG_TOKEN`, `TG_CHAT_ID`, `TG_PERSONAL_ID`, плюс FTP-секреты деплоя. Ручной запуск: Actions → «Ежедневный выпуск» → Run workflow.

Локальный запуск (`./run.sh`, `morning.sh`) по-прежнему работает, но cron на маке нужно выключить, чтобы не было двойных выпусков.

**Scheduling:**
- `morning.sh` — идемпотентный триггер: если `docs/data/<today>.json` уже есть, выходит мгновенно. Иначе автоматически выбирает самый дальний кэш (`--from-filter` / `--from-raw`) и запускает `run.sh`. Три запуска в день гарантируют выпуск даже после одиночного сбоя.
- `run.sh` — держит `flock` на `/tmp/neurogazette.lock` (повторные запуски no-op), шлёт Telegram-алерт в личку при любом падении (через `trap ERR` + переменная `CURRENT_STAGE`).

## Environment / secrets

Create `.env` in the repo root (gitignored) for local use:
```
TG_TOKEN=...
TG_CHAT_ID=...       # public channel
TG_PERSONAL_ID=...   # personal notification
```

The pipeline uses the Claude Code CLI subscription — no `ANTHROPIC_API_KEY` needed for `run.sh`.
