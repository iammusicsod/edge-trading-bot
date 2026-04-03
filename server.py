import http.server
import json
import os
from pathlib import Path

BOT_DIR = Path(__file__).parent
PORT = 8080

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        try:
            path = self.path.split('?')[0]
            
            if path == '/summary':
                f = BOT_DIR / 'summary.json'
                if f.exists():
                    try:
                        raw = f.read_bytes()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.send_header('Content-Length', str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    except Exception as e:
                        self.send_response(500)
                        self.send_header('Content-Type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(str(e).encode())
                else:
                    body = b'{"summary":"Waiting for first scan...","time":"","signals":{}}'
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return

            if path == '/' or path == '/index.html':
                body = open(BOT_DIR / 'dashboard.html', 'rb').read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == '/state':
                f = BOT_DIR / 'state.json'
                raw = f.read_bytes() if f.exists() else b'{}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/log':
                f = BOT_DIR / 'bot_log.txt'
                body = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == '/explanations':
                f = BOT_DIR / 'trade_explanations.json'
                raw = f.read_bytes() if f.exists() else b'[]'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/shadow':
                f = BOT_DIR / 'shadow_shorts.csv'
                raw = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/shadowlong':
                f = BOT_DIR / 'shadow_longs.csv'
                raw = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/audit':
                f = BOT_DIR / 'strategy_audit.csv'
                raw = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/rejected':
                f = BOT_DIR / 'rejected_signals.csv'
                raw = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/equity':
                f = BOT_DIR / 'equity_curve.csv'
                raw = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/symbols':
                f = BOT_DIR / 'symbol_performance.csv'
                raw = f.read_bytes() if f.exists() else b''
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            self.send_response(404)
            self.end_headers()

        except Exception as e:
            try:
                msg = str(e).encode('utf-8', errors='replace')
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            except:
                pass

if __name__ == '__main__':
    server = http.server.HTTPServer(('', PORT), Handler)
    print(f'Dashboard server running on port {PORT}')
    server.serve_forever()
