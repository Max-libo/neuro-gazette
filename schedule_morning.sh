#!/usr/bin/env bash
# Ставит отложенный запуск пайплайна на 07:00 МСК следующего утра.
# Инжектирует "запускай" в терминал Claude Code — как будто пользователь написал сам.
# Запускается каждый вечер по крону в 21:00 МСК.

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/pipeline.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# Найти pts Claude Code процесса
PTS=$(ls -la /proc/$(pgrep -f "^claude$" | head -1)/fd/0 2>/dev/null | grep -o '/dev/pts/[0-9]*')

if [ -z "$PTS" ]; then
  log "Не найден pts Claude Code — запланирован прямой запуск run.sh"
  echo "bash $REPO/run.sh >> $LOG 2>&1" | TZ=Europe/Moscow at 07:00 2>&1 | tee -a "$LOG"
else
  log "Claude Code на $PTS — запланирован инжект 'запускай' в 07:00"
  cat << EOF > /tmp/inject_morning.py
import fcntl, termios
with open('$PTS', 'w') as f:
    for char in 'запускай\r':
        fcntl.ioctl(f, termios.TIOCSTI, char.encode())
EOF
  echo "python3 /tmp/inject_morning.py" | TZ=Europe/Moscow at 07:00 2>&1 | tee -a "$LOG"
fi

log "Готово."
