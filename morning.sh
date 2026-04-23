#!/usr/bin/env bash
# Утренний триггер нейрогазеты.
# Запускается по крону 3 раза в день (07:00, 10:00, 14:00 МСК).
# Проверяет, опубликован ли уже сегодняшний выпуск, и при необходимости
# перезапускает пайплайн, переиспользуя кэш этапов 1-3.
#
# Идемпотентен: если выпуск за сегодня уже в docs/data/, выходит без действий.

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/pipeline.log"
DATA="$REPO/docs/data"
DATE=$(TZ=Europe/Moscow date +%Y-%m-%d)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

if [ -f "$DATA/${DATE}.json" ]; then
  log "Выпуск $DATE уже опубликован — пропускаю."
  exit 0
fi

# Выбираем самый дальний кэш, до которого пайплайн дошёл
ARGS=""
if   [ -f "$DATA/${DATE}_filtered.txt" ]; then ARGS="--from-filter"
elif [ -f "$DATA/${DATE}_search.json"  ]; then ARGS="--from-raw"
elif [ -f "$DATA/${DATE}_raw.json"     ]; then ARGS="--from-raw"
fi

log "Запуск пайплайна ($DATE) ${ARGS:-полный старт}…"
bash "$REPO/run.sh" $ARGS >> "$LOG" 2>&1
log "morning.sh: готово."
