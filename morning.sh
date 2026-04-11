#!/usr/bin/env bash
# Утренний триггер нейрогазеты.
# Отправляет сообщение в текущую сессию Claude Code — как будто пользователь написал вручную.
# Запускается по крону в 04:00 UTC (07:00 МСК).

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/pipeline.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "Запуск утреннего пайплайна…"
bash "$REPO/run.sh" >> "$LOG" 2>&1
log "Готово."

# Личное уведомление об окончании
if [ -f "$REPO/.env" ]; then set -a; source "$REPO/.env"; set +a; fi
if [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_PERSONAL_ID:-}" ]; then
  DATE=$(date +%Y-%m-%d)
  STATUS="✅ Выпуск $DATE собран и опубликован"
  curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -d chat_id="${TG_PERSONAL_ID}" \
    -d text="${STATUS}" > /dev/null
  log "Личное уведомление отправлено."
fi
