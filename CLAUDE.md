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
| 2 | `stage2_search.py` | `sources.yaml` search_queries | `{date}_search.json` | Sonnet 5.5 (WebSearch tool via CLI) |
| 3 | `stage3_filter.py` | `_raw.json` + `_search.json` | `{date}_filtered.txt` | Sonnet 5.5 (via CLI) |
| 4 | `stage4_edit.py` | `_filtered.txt` + `_search.json` | `{date}.json`, `latest.json` | Opus 5.5 (via CLI) |

Модели заданы в одном месте — `MODEL_FAST` / `MODEL_EDIT` в `common.py`; переопределяются переменными `NEUROGAZETTE_MODEL_FAST` / `_EDIT` / `_PING` (в Actions — Settings → Variables, без правки кода). Healthcheck в `run.sh` — Haiku 5.5. Нужен Claude Code CLI ≥ 2.1.293 (в Actions ставится свежий; на маке — `claude update`).

After stage 4, `generate_preview.py` builds OG-preview HTML in `docs/preview/`, then `run.sh` commits `docs/data/` and `docs/preview/` and optionally notifies Telegram.

**Shared code:** `scripts/pipeline/common.py` — time window calculation, URL validation, date parsing, `validate_and_fix()`, `finalize_news()`, `run_claude()` (wraps `claude --print`), changelog versioning.

**`run_claude()`** calls `claude --print --model <model>` via subprocess, reads stdin. Rate-limit handling: waits 60 min and retries up to 6 times.

### Stage 1: collect

Parallel HTTP fetch (ThreadPoolExecutor, 10 workers) from all `sources` in `sources.yaml`. Source types:
- `rss` — parsed with feedparser
- `scrape` — HTML parsed with BeautifulSoup; tries JSON-LD structured data first, falls back to `h1/h2/h3` scan

Items filtered by 24h window, deduplicated by URL and title. **Защита от сборки без сети:** если не загрузилось больше половины источников (`NEUROGAZETTE_MAX_FAILED_SOURCES`, по умолчанию 0.5), этап 1 падает без записи `_raw.json` — `run.sh` шлёт алерт, self-heal-запуск повторяет. Так урезанная сеть (облачная сессия, сбой прокси) не превращается в тощий выпуск. Results sorted by `priority` (1=highest), truncated to `RAW_LIMIT=200` — but up to `RAW_CONTENT_RESERVE=40` places are guaranteed to `section: content` and `section: art` sources (`RAW_ART_RESERVE=10` of them to `art`; they are usually priority 2–3 and would otherwise be cut before filtering).

Optional per-source `keywords:` list — only items whose title/summary match are kept. Used for general art media where AI is a small share. `"нейросет*"` = prefix match, `"AI"` = whole word.

`is_vague_url()` also rejects service pages (`/home`, `/pricing`, `/press-inquiry`…) and SEO listicles (`/best-…`, `/top-10-…`).

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
  "section": "models|platforms|industry|content|art|hype",
  "headline": "...",
  "subheadline": "...",
  "body": "...",
  "tier": "hero|regular|compact",
  "importance": 9,
  "sources": [{"title": "...", "url": "...", "type": "official|media|rumor"}],
  "related": [{"title": "...", "url": "...", "entities": [], "sentiment": "positive|negative|neutral"}],
  "unconfirmed": false,
  "duplicate_note": null,
  "details": "content/art only, optional: «Москва, ГЭС-2 · 20 окт — 15 янв» / «−40% на год · до 31 окт»",
  "tags": {"entities": [], "sentiment": "positive|negative|neutral|rumor", "event": "release|update|shutdown|investment|regulation|leak|promo|exhibition"}
}
```

### Rubrics
`models` Модели · `platforms` Платформы · `industry` Индустрия · `content` Нейроконтент · `art` Искусство · `hype` Желтуха.

**Нейроконтент (`content`)** — нейросети, которыми пользуются нейрокреаторы: генерация фото, видео, 3D-моделей, миров и игр (Midjourney, Flux, Nano Banana, GPT-image, Kandinsky, Veo, Sora, Kling, Runway, Seedance, Meshy, Genie…) — релизы и обновления, бесплатные генераторы и тарифы, скидки на подписки. Универсальные мультимодальные модели остаются в `models`; деньги, суды и кадры вокруг этих компаний — в `industry`/`hype`. Скидки — только объявленные самим сервисом, с размером и сроком, всегда `compact` + `event: promo`; «бесплатно» — с указанием лимита.

**Искусство (`art`)** — выставки, нейромузеи, фестивали, конкурсы и премии ИИ-искусства (мир + Россия); только с площадкой и датами, `event: exhibition`. `details` (практическая строка: место и даты, срок скидки, лимит) сохраняется только для `content` и `art`.

### Source health check
`scripts/check_sources.py [sections…]` — HTTP-only, no Claude, 30-day window, same fetch functions as stage 1. In CI: Actions → «Проверка источников» → Run workflow (default `content`); report lands in the run Summary.

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
