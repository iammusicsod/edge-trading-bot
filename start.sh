#!/bin/bash
echo "Starting EDGE Trading Bot..."
cd ~/trading-bot
python3 bot.py &
sleep 2
python3 server.py &
echo "✅ Bot and dashboard running!"
echo "Open Safari and go to: localhost:8080"
wait
