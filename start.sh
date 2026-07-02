#!/bin/bash
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECT_DIR="$HOME/.huntflow"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/venv/bin/activate"

echo "🚀 Запуск бота..."

kill -9 $(pgrep -f "python.*bot.py") 2>/dev/null

nohup python bot.py > /tmp/huntflow.log 2>&1 &
BOT_PID=$!
echo "✅ Бот запущен (PID: $BOT_PID)"
echo "📋 Логи: tail -f /tmp/huntflow.log"
echo ""
echo "🌐 Вебхук URL на PythonAnywhere:"
echo "   https://your-username.pythonanywhere.com/huntflow-webhook"
echo ""
echo "🛑 Остановка: kill $BOT_PID"
