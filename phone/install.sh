#!/data/data/com.termux/files/usr/bin/bash
# VibeCloud на Android (Termux). Один раз: bash phone/install.sh
set -e
cd "$(dirname "$0")/.."

echo "▶ Обновляю пакеты Termux…"
pkg update -y && pkg upgrade -y
# python-pillow и готовые библиотеки из репозитория Termux — чтобы не собирать их на телефоне
pkg install -y python python-pip python-pillow ffmpeg git clang make rust binutils cloudflared termux-api || \
pkg install -y python python-pip python-pillow ffmpeg git clang make rust binutils cloudflared

echo "▶ Ставлю Python-зависимости (первый раз 10–20 минут: собирается pydantic-core)…"
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install -U wheel setuptools
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  # на телефоне: свой адрес выдаёт туннель, short name не нужен, yt-dlp обновляем сами
  sed -i 's/^WEBAPP_SHORT_NAME=.*/WEBAPP_SHORT_NAME=/' .env
  sed -i 's/^YTDLP_AUTOUPDATE=.*/YTDLP_AUTOUPDATE=1/' .env
  sed -i 's/^DB_BACKUP_MIN=.*/DB_BACKUP_MIN=5/' .env
  echo ""
  echo "✎ Открой .env и впиши BOT_TOKEN, STORAGE_CHAT_ID, ADMIN_IDS (и AI_API_KEY для Ewa):"
  echo "    nano .env"
fi

# автозапуск после перезагрузки телефона (нужно приложение Termux:Boot из F-Droid)
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/vibecloud.sh <<BOOT
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd "$(pwd)" && bash phone/start.sh >> data/vibecloud.log 2>&1
BOOT
chmod +x ~/.termux/boot/vibecloud.sh phone/*.sh
echo ""
echo "✓ Готово. Запуск: bash phone/start.sh"
