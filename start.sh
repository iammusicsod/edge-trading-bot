#!/bin/bash
echo "Starting EDGE Bot v7..."
echo ""

# Kill anything already running on port 8080
lsof -ti:8080 | xargs kill -9 2>/dev/null

# Start server in background
cd ~/trading-bot
python3 server.py &
SERVER_PID=$!
echo "✅ Dashboard server started (PID $SERVER_PID)"
echo "   Open Safari → localhost:8080"
echo ""

# Short pause then start bot
sleep 1
echo "✅ Starting bot..."
echo ""
python3 bot.py

# When bot stops, kill server too
kill $SERVER_PID 2>/dev/null
