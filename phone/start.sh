#!/data/data/com.termux/files/usr/bin/bash
# Запуск VibeCloud на телефоне: свой туннель Cloudflare (HTTPS для Mini App) + бот с автоперезапуском.
# Трогает только свои процессы — другие проекты в Termux (VibeNews и т.п.) продолжают работать.
cd "$(dirname "$0")/.."
mkdir -p data
termux-wake-lock 2>/dev/null || true
[ -f .venv/bin/activate ] && . .venv/bin/activate

# порт берём из окружения или из .env (по умолчанию 8090)
if [ -z "$PORT" ]; then PORT="$(grep -E '^PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' "\r')"; fi
PORT="${PORT:-8090}"
export PORT

# не запускаем второй экземпляр VibeCloud
if [ -f data/vibecloud.pid ] && kill -0 "$(cat data/vibecloud.pid)" 2>/dev/null; then
  echo "VibeCloud уже запущен (PID $(cat data/vibecloud.pid)). Остановить: bash phone/stop.sh"
  exit 0
fi
echo $$ > data/vibecloud.pid

STOP=0; APP=""
cleanup() {
  [ -f data/tunnel.pid ] && kill "$(cat data/tunnel.pid)" 2>/dev/null
  rm -f data/tunnel.pid data/vibecloud.pid
}
# Ctrl+C / stop.sh: передаём сигнал боту и ждём, пока он сохранит финальную копию базы
on_stop() { STOP=1; [ -n "$APP" ] && kill -TERM "$APP" 2>/dev/null; }
trap on_stop INT TERM
trap cleanup EXIT

if [ -z "$PUBLIC_URL" ] && ! grep -q '^PUBLIC_URL=https' .env 2>/dev/null; then
  echo "▶ Поднимаю свой HTTPS-туннель на порт $PORT…"
  [ -f data/tunnel.pid ] && kill "$(cat data/tunnel.pid)" 2>/dev/null   # только НАШ старый туннель
  : > data/tunnel.log
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > data/tunnel.log 2>&1 &
  echo $! > data/tunnel.pid
  for i in $(seq 1 60); do
    URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' data/tunnel.log | head -1)"
    [ -n "$URL" ] && break
    sleep 1
  done
  if [ -z "$URL" ]; then
    echo "✗ Туннель не поднялся — смотри data/tunnel.log (включён ли VPN?). Бот запустится без Mini App."
  else
    export PUBLIC_URL="$URL"
    echo "✓ Mini App: $URL  (кнопка меню бота обновится сама)"
  fi
fi

while [ "$STOP" = 0 ]; do
  python -m app.main &
  APP=$!
  wait "$APP"; wait "$APP" 2>/dev/null
  APP=""
  [ "$STOP" = 1 ] && break
  echo "⟳ VibeCloud остановился — перезапуск через 5 секунд (Ctrl+C — выход)"
  sleep 5
done
echo "VibeCloud остановлен"
