#!/usr/bin/env bash
# Нейрогазета — оркестратор пайплайна на подписке Claude.
# Использует `claude --print` вместо API-ключа.
#
# Использование:
#   ./run.sh              — полный запуск
#   ./run.sh --from-raw   — пропустить этапы 1-2 (использовать кэш)
#   ./run.sh --from-filter — пропустить этапы 1-3
#   ./run.sh --no-push    — не коммитить и не пушить
#
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS="$REPO/scripts/pipeline"
FROM_RAW=false
FROM_FILTER=false
NO_PUSH=false

for arg in "$@"; do
  case "$arg" in
    --from-raw)    FROM_RAW=true ;;
    --from-filter) FROM_FILTER=true ;;
    --no-push)     NO_PUSH=true ;;
  esac
done

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Проверка зависимостей ─────────────────────────────────────────────────────
if ! command -v claude &>/dev/null; then
  echo "Ошибка: claude CLI не найден. Установите Claude Code и войдите в аккаунт."
  exit 1
fi

cd "$REPO"

# ── Этап 1: Сбор RSS/scrape ───────────────────────────────────────────────────
if $FROM_RAW || $FROM_FILTER; then
  log "Этап 1: пропущен (--from-raw / --from-filter)"
else
  log "Этап 1: сбор RSS и scrape…"
  python3 "$SCRIPTS/stage1_collect.py"
fi

# ── Этап 2: Веб-поиск ─────────────────────────────────────────────────────────
if $FROM_FILTER; then
  log "Этап 2: пропущен (--from-filter)"
elif $FROM_RAW; then
  # В режиме from-raw поиск тоже пропускаем (берём кэш если есть)
  DATE=$(python3 -c "import sys; sys.path.insert(0,'scripts/pipeline'); from common import TODAY_STR; print(TODAY_STR)" 2>/dev/null || date +%Y-%m-%d)
  SEARCH_CACHE="docs/data/${DATE}_search.json"
  if [ -f "$SEARCH_CACHE" ]; then
    log "Этап 2: используем кэш $SEARCH_CACHE"
  else
    log "Этап 2: нет кэша, запускаем поиск…"
    python3 "$SCRIPTS/stage2_search.py"
  fi
else
  log "Этап 2: веб-поиск…"
  python3 "$SCRIPTS/stage2_search.py"
fi

# ── Этап 3: Фильтрация ────────────────────────────────────────────────────────
if $FROM_FILTER; then
  log "Этап 3: пропущен (--from-filter)"
else
  log "Этап 3: фильтрация…"
  python3 "$SCRIPTS/stage3_filter.py"
fi

# ── Этап 4: Редактура ─────────────────────────────────────────────────────────
log "Этап 4: редактура (Opus)…"
python3 "$SCRIPTS/stage4_edit.py"

# ── Git commit + push ─────────────────────────────────────────────────────────
if $NO_PUSH; then
  log "Готово. Git push пропущен (--no-push)."
  exit 0
fi

log "Коммит…"
git config user.name  "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add docs/data/

if git diff --cached --quiet; then
  log "Нет изменений для коммита."
  exit 0
fi

DATE=$(python3 -c "import json; print(json.load(open('docs/data/latest.json'))['date'])")
git commit -m "feat: выпуск $DATE"
git pull --rebase
git push

log "Готово: выпуск $DATE опубликован."
