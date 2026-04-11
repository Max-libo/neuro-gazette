#!/usr/bin/env bash
# Утренний триггер нейрогазеты.
# Отправляет сообщение в текущую сессию Claude Code — как будто пользователь написал вручную.
# Запускается по крону в 04:00 UTC (07:00 МСК).

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/pipeline.log"
SESSION_FILE="$HOME/.claude/sessions"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# Найти активную сессию в директории репозитория
SESSION_ID=""
if [ -d "$HOME/.claude/sessions" ]; then
  for f in "$HOME/.claude/sessions/"*.json; do
    [ -f "$f" ] || continue
    cwd=$(python3 -c "import json; d=json.load(open('$f')); print(d.get('cwd',''))" 2>/dev/null)
    if [ "$cwd" = "$REPO" ]; then
      SESSION_ID=$(python3 -c "import json; d=json.load(open('$f')); print(d.get('sessionId',''))" 2>/dev/null)
      break
    fi
  done
fi

if [ -z "$SESSION_ID" ]; then
  log "Активная сессия не найдена — запускаю run.sh напрямую"
  bash "$REPO/run.sh" >> "$LOG" 2>&1
  exit $?
fi

log "Отправляю сообщение в сессию $SESSION_ID"
claude \
  --resume "$SESSION_ID" \
  --print \
  --allowedTools "Bash" \
  --permission-mode bypassPermissions \
  -p "Запусти утренний пайплайн: выполни bash $REPO/run.sh и дождись завершения" \
  >> "$LOG" 2>&1

log "Готово."
