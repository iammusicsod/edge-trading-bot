#!/usr/bin/env python3
import threading
import os
import sys

def run_server():
    import json
    from pathlib import Path
    from http.server import HTTPServer, BaseHTTPRequestHandler

    BOT_DIR = Path(__file__).parent

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args): pass
        def do_GET(self):
            if self.path in ['/', '/index.html']:
                self.serve('dashboard.html', 'text/html')
            elif self.path == '/state':
                self.serve_state()
            elif self.path == '/log':
                self.serve_log()
            elif self.path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'OK')
            else:
                self.send_response(404)
                self.end_headers()

        def serve(self, name, ctype):
            p = BOT_DIR / name
            if p.exists():
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(p.read_bytes())

        def serve_state(self):
            p = BOT_DIR / 'state.json'
            data = p.read_text() if p.exists() else '{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data.encode())

        def serve_log(self):
            p = BOT_DIR / 'bot_log.txt'
            lines = p.read_text().splitlines()[-80:] if p.exists() else ['No log yet.']
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write('\n'.join(lines).encode())

    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Dashboard running on port {port}')
    server.serve_forever()

def run_bot():
    import bot
    bot.main()

if __name__ == '__main__':
    # Start server in background thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print('Server started in background')
    # Run bot in main thread
    run_bot()
