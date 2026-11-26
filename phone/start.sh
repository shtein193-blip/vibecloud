#!/data/data/com.termux/files/usr/bin/bash
# Запуск VibeCloud на телефоне: туннель Cloudflare (HTTPS для Mini App) + бот с автоперезапуском.
cd "$(dirname "$0")/.."
mkdir -p data
termux-wake-lock 2>/dev/null || true   # не даём Android усыпить процесс
PORT="${PORT:-8080}"
export PORT

if [ -z "$PUBLIC_URL" ] && ! grep -q '^PUBLIC_URL=https' .env 2>/dev/null; then
  echo "▶ Поднимаю HTTPS-туннель…"
  pkill -f "cloudflared tunnel" 2>/dev/null
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > data/tunnel.log 2>&1 &
  for i in $(seq 1 60); do
    URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' data/tunnel.log | head -1)"
    [ -n "$URL" ] && break
    sleep 1
  done
  if [ -z "$URL" ]; then
    echo "✗ Туннель не поднялся — смотри data/tunnel.log. Бот запустится без Mini App."
  else
    export PUBLIC_URL="$URL"
    echo "✓ Mini App: $URL  (кнопка меню бота обновится сама)"
  fi
fi

while true; do
  python -m app.main
  echo "⟳ VibeCloud остановился — перезапуск через 5 секунд (Ctrl+C — выход)"
  sleep 5
done
