#!/usr/bin/env bash
# Ставит прямой запуск пайплайна на 07:00 МСК следующего утра через at.
# Запускается каждый вечер по крону в 18:00 МСК.

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/pipeline.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

echo "bash $REPO/run.sh >> $LOG 2>&1" | TZ=Europe/Moscow at 07:00 2>&1 | tee -a "$LOG"
log "Запланирован прямой запуск run.sh в 07:00 МСК."
