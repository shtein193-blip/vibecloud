#!/data/data/com.termux/files/usr/bin/bash
# Остановить только VibeCloud. Бот успеет сохранить финальную копию базы в канал (до ~20 с).
cd "$(dirname "$0")/.."
if [ -f data/vibecloud.pid ] && kill -0 "$(cat data/vibecloud.pid)" 2>/dev/null; then
  kill -TERM "$(cat data/vibecloud.pid)"
  for i in $(seq 1 30); do kill -0 "$(cat data/vibecloud.pid 2>/dev/null || echo 0)" 2>/dev/null || break; sleep 1; done
  echo "VibeCloud остановлен"
else
  echo "VibeCloud не запущен"
fi
