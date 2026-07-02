#!/bin/bash
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECT_DIR="$HOME/.huntflow"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/venv/bin/activate"

echo "🚀 Запуск бота и проверки просрочек..."

kill -9 $(pgrep -f "python.*bot.py") 2>/dev/null

nohup python bot.py > /tmp/huntflow.log 2>&1 &
BOT_PID=$!
echo "✅ Бот запущен (PID: $BOT_PID)"

# Загрузить launchd таймер для overdue_checker (раз в час)
cp "$PROJECT_DIR/com.huntflow.overdue.plist" "$HOME/Library/LaunchAgents/"
launchctl load "$HOME/Library/LaunchAgents/com.huntflow.overdue.plist" 2>/dev/null
echo "✅ Таймер просрочек запущен (каждый час)"

echo ""
echo "📋 Логи бота:    tail -f /tmp/huntflow.log"
echo "📋 Логи просрочек: tail -f /tmp/huntflow-overdue.log"
echo "🌐 Вебхук URL: https://ratpoisen.pythonanywhere.com/huntflow-webhook"
echo "🛑 Остановка:    kill $BOT_PID"
echo "❌ Откл. таймер: launchctl unload ~/Library/LaunchAgents/com.huntflow.overdue.plist"
