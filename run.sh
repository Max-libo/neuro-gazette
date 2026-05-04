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

# ── Single-instance lock: не стартуем повторно, если уже идёт ────────────────
LOCK_FILE=/tmp/neurogazette.lock
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date '+%H:%M:%S')] run.sh уже выполняется (lock $LOCK_FILE) — выхожу."
  exit 0
fi

# Загружаем .env если есть (TG_TOKEN, TG_CHAT_ID, TG_PERSONAL_ID)
if [ -f "$REPO/.env" ]; then set -a; source "$REPO/.env"; set +a; fi
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

# ── Telegram-алерт в личку ────────────────────────────────────────────────────
CURRENT_STAGE="init"
tg_send_personal() {
  local text="$1"
  if [ -z "${TG_TOKEN:-}" ] || [ -z "${TG_PERSONAL_ID:-}" ]; then
    log "TG: TG_TOKEN или TG_PERSONAL_ID не заданы — алерт не отправлен"
    return 0
  fi
  local resp
  resp=$(curl -sS --max-time 15 -X POST \
    "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -d chat_id="${TG_PERSONAL_ID}" \
    -d text="$text" 2>&1) || {
      log "TG: curl упал ($?): $resp"
      return 0
    }
  if [[ "$resp" != *'"ok":true'* ]]; then
    log "TG: API ответил не ok: $resp"
  fi
}

notify_failure() {
  local stage="${1:-$CURRENT_STAGE}"
  log "FAIL: стадия «$stage»"
  tg_send_personal "❌ Нейрогазета: пайплайн упал на стадии «${stage}». Смотри pipeline.log"
}
trap 'notify_failure "$CURRENT_STAGE"' ERR

# ── Проверка зависимостей ─────────────────────────────────────────────────────
if ! command -v claude &>/dev/null; then
  echo "Ошибка: claude CLI не найден. Установите Claude Code и войдите в аккаунт."
  exit 1
fi

cd "$REPO"

# ── Healthcheck Claude CLI ───────────────────────────────────────────────────
# Защита от длинной серии 403: лучше упасть сразу с понятным алертом,
# чем 8 минут крутить три попытки ретраев на каждой стадии.
CURRENT_STAGE="healthcheck"
log "Healthcheck: проверяю авторизацию claude CLI…"
HEALTH_OUT=$(timeout 30 claude --print --model claude-haiku-4-5-20251001 'ping' 2>&1) || HEALTH_RC=$?
HEALTH_RC=${HEALTH_RC:-0}
if [ "$HEALTH_RC" -ne 0 ] || [[ "$HEALTH_OUT" == *"403"* ]] || [[ "$HEALTH_OUT" == *"Failed to authenticate"* ]]; then
  log "Healthcheck: claude CLI не авторизован. Ответ: ${HEALTH_OUT:0:200}"
  tg_send_personal "🔑 Нейрогазета: Claude CLI не авторизован (403/Failed to authenticate). Зайди на сервер и обнови подписку: claude /login. До этого утренние выпуски собираться не будут."
  trap - ERR
  exit 0
fi
log "Healthcheck: OK"

# ── Этап 1: Сбор RSS/scrape ───────────────────────────────────────────────────
if $FROM_RAW || $FROM_FILTER; then
  log "Этап 1: пропущен (--from-raw / --from-filter)"
else
  log "Этап 1: сбор RSS и scrape…"
  CURRENT_STAGE="stage1_collect"
  python3 "$SCRIPTS/stage1_collect.py"
fi

# ── Этап 2: Веб-поиск ─────────────────────────────────────────────────────────
if $FROM_FILTER; then
  log "Этап 2: пропущен (--from-filter)"
elif $FROM_RAW; then
  DATE=$(python3 -c "import sys; sys.path.insert(0,'scripts/pipeline'); from common import TODAY_STR; print(TODAY_STR)" 2>/dev/null || date +%Y-%m-%d)
  SEARCH_CACHE="docs/data/${DATE}_search.json"
  if [ -f "$SEARCH_CACHE" ]; then
    log "Этап 2: используем кэш $SEARCH_CACHE"
  else
    log "Этап 2: нет кэша, запускаем поиск…"
    CURRENT_STAGE="stage2_search"
    python3 "$SCRIPTS/stage2_search.py"
  fi
else
  log "Этап 2: веб-поиск…"
  CURRENT_STAGE="stage2_search"
  python3 "$SCRIPTS/stage2_search.py"
fi

# ── Этап 3: Фильтрация ────────────────────────────────────────────────────────
if $FROM_FILTER; then
  log "Этап 3: пропущен (--from-filter)"
else
  log "Этап 3: фильтрация…"
  CURRENT_STAGE="stage3_filter"
  python3 "$SCRIPTS/stage3_filter.py"
fi

# ── Этап 4: Редактура ─────────────────────────────────────────────────────────
log "Этап 4: редактура (Opus)…"
CURRENT_STAGE="stage4_edit"
python3 "$SCRIPTS/stage4_edit.py"

# ── Превью для Telegram (OG-картинка + HTML) ─────────────────────────────────
log "Генерация OG-превью…"
CURRENT_STAGE="preview"
python3 "$REPO/scripts/generate_preview.py"

# ── Git commit + push ─────────────────────────────────────────────────────────
if $NO_PUSH; then
  log "Готово. Git push пропущен (--no-push)."
  exit 0
fi

CURRENT_STAGE="git_commit"
log "Коммит…"
git config user.name  "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add docs/data/ docs/preview/ sources.yaml

if git diff --cached --quiet; then
  log "Нет изменений для коммита."
  exit 0
fi

DATE=$(python3 -c "import json; print(json.load(open('docs/data/latest.json'))['date'])")
git commit -m "feat: выпуск $DATE"

CURRENT_STAGE="git_push"
# --autostash убирает посторонние локальные правки на время rebase и возвращает обратно
git pull --rebase --autostash || log "git pull --rebase не удался, пытаюсь push как есть"
git push

log "Готово: выпуск $DATE опубликован."

# ── Telegram-уведомление в канал ─────────────────────────────────────────────
CURRENT_STAGE="tg_channel"
if [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
  python3 "$REPO/scripts/tg_notify.py" "$DATE" \
    && log "Telegram: уведомление в канал отправлено." || log "Telegram: ошибка отправки в канал."
fi

# ── Личное Telegram-уведомление ──────────────────────────────────────────────
CURRENT_STAGE="tg_personal"
if [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_PERSONAL_ID:-}" ]; then
  curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -d chat_id="${TG_PERSONAL_ID}" \
    -d text="✅ Выпуск $DATE собран и опубликован" > /dev/null \
    && log "Telegram: личное уведомление отправлено." || log "Telegram: ошибка личного уведомления."
fi

# Снимаем trap перед чистым выходом, чтобы последний exit не триггерил ERR
trap - ERR
