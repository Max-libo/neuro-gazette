#!/usr/bin/env bash
# Ставит отложенный запуск пайплайна на 07:00 МСК следующего утра.
# Запускается каждый вечер по крону — передаёт текущее окружение (токен Claude).
# Запускать вручную тоже можно: bash schedule_morning.sh

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/pipeline.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# Экспортируем переменные окружения в задание at
# (at по умолчанию не наследует окружение — передаём явно)
ENV_VARS=""
for var in HOME PATH CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDECODE; do
  val="${!var:-}"
  [ -n "$val" ] && ENV_VARS="export $var='$val'; $ENV_VARS"
done

JOB="${ENV_VARS} bash $REPO/run.sh >> $LOG 2>&1"

echo "$JOB" | TZ=Europe/Moscow at 07:00 2>&1 | tee -a "$LOG"
log "Пайплайн запланирован на 07:00 МСК."
