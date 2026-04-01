#!/usr/bin/env python3
import json
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_DIR = Path(__file__).parent

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        if self.path == '/':
            self.serve('dashboard.html', 'text/html')
        elif self.path == '/state':
            self.serve_state()
        elif self.path == '/log':
            self.serve_log()
        else:
            self.send_response(404); self.end_headers()
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
        lines = p.read_text().splitlines()[-60:] if p.exists() else ['No log yet.']
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write('\n'.join(lines).encode())

if __name__ == '__main__':
    server = HTTPServer(('localhost', 8080), Handler)
    print('Dashboard ready — open http://localhost:8080 in your browser')
    server.serve_forever()
