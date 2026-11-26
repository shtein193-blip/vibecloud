#!/data/data/com.termux/files/usr/bin/bash
# VibeCloud на Android (Termux). Один раз: bash phone/install.sh
# Уживается с другими проектами в том же Termux (например, VibeNews): свои библиотеки — в отдельном .venv,
# свой туннель, свой скрипт автозапуска.
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "▶ Ставлю системные пакеты (уже установленные просто пропустятся)…"
pkg install -y python python-pip python-pillow ffmpeg clang make rust binutils cloudflared || true

echo "▶ Отдельное окружение Python для VibeCloud (.venv) — библиотеки других ботов не трогаем…"
[ -d .venv ] || python -m venv --system-site-packages .venv
. .venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install -U wheel setuptools
echo "▶ Ставлю зависимости (первый раз 10–20 минут: собирается pydantic-core)…"
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i 's/^WEBAPP_SHORT_NAME=.*/WEBAPP_SHORT_NAME=/' .env
  sed -i 's/^YTDLP_AUTOUPDATE=.*/YTDLP_AUTOUPDATE=1/' .env
  sed -i 's/^DB_BACKUP_MIN=.*/DB_BACKUP_MIN=5/' .env
  echo "PORT=8090" >> .env   # не 8080: этот порт часто занят другими проектами
  echo ""
  echo "✎ Открой .env и впиши BOT_TOKEN, STORAGE_CHAT_ID, ADMIN_IDS (и AI_API_KEY для Ewa):"
  echo "    nano .env"
fi

# автозапуск после перезагрузки (Termux:Boot). Запускаем в фоне, чтобы не блокировать
# скрипты автозапуска других проектов в той же папке.
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/vibecloud.sh <<BOOT
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd "$ROOT" && nohup bash phone/start.sh >> data/vibecloud.log 2>&1 &
BOOT
chmod +x ~/.termux/boot/vibecloud.sh phone/*.sh
mkdir -p data
echo ""
echo "✓ Готово. Запуск: bash phone/start.sh"
