import http.server
import json
import csv
from pathlib import Path

BOT_DIR = Path(__file__).parent
PORT = 8080

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send(self, code, body, ctype='application/json'):
        if isinstance(body, str):
            body = body.encode('utf-8', errors='replace')
        self.send_response(code)
        self.send_header('Content-Type', ctype + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, filepath, default):
        try:
            if Path(filepath).exists():
                raw = Path(filepath).read_bytes()
                text = raw.decode('utf-8', errors='replace')
                return json.loads(text)
            return default
        except:
            return default

    def read_csv(self, filepath):
        rows = []
        try:
            if Path(filepath).exists():
                with open(filepath, newline='', encoding='utf-8', errors='replace') as f:
                    for row in csv.DictReader(f):
                        rows.append(dict(row))
        except:
            pass
        return rows

    def do_GET(self):
        try:
            path = self.path.split('?')[0]

            if path in ('/', '/index.html'):
                f = BOT_DIR / 'dashboard.html'
                body = f.read_bytes() if f.exists() else b'Not found'
                self.send(200, body, 'text/html')

            elif path == '/state':
                data = self.read_json(BOT_DIR / 'state.json', {})
                self.send(200, json.dumps(data, ensure_ascii=True).encode())

            elif path == '/summary':
                data = self.read_json(BOT_DIR / 'summary.json', {'summary': 'Waiting for first scan...', 'time': '', 'signals': {}})
                self.send(200, json.dumps(data, ensure_ascii=True).encode())

            elif path == '/log':
                f = BOT_DIR / 'bot_log.txt'
                body = f.read_bytes() if f.exists() else b''
                self.send(200, body, 'text/plain')

            elif path == '/explanations':
                data = self.read_json(BOT_DIR / 'trade_explanations.json', [])
                self.send(200, json.dumps(data, ensure_ascii=True).encode())

            elif path == '/shadow':
                self.send(200, json.dumps(self.read_csv(BOT_DIR / 'shadow_shorts.csv'), ensure_ascii=True).encode())

            elif path == '/shadowlong':
                self.send(200, json.dumps(self.read_csv(BOT_DIR / 'shadow_longs.csv'), ensure_ascii=True).encode())

            elif path == '/audit':
                self.send(200, json.dumps(self.read_csv(BOT_DIR / 'strategy_audit.csv'), ensure_ascii=True).encode())

            elif path == '/rejected':
                self.send(200, json.dumps(self.read_csv(BOT_DIR / 'rejected_signals.csv'), ensure_ascii=True).encode())

            elif path == '/equity':
                self.send(200, json.dumps(self.read_csv(BOT_DIR / 'equity_curve.csv'), ensure_ascii=True).encode())

            elif path == '/symbols':
                self.send(200, json.dumps(self.read_csv(BOT_DIR / 'symbol_performance.csv'), ensure_ascii=True).encode())

            else:
                self.send(404, b'Not found', 'text/plain')

        except Exception as e:
            try:
                self.send(500, str(e).encode('utf-8', errors='replace'), 'text/plain')
            except:
                pass

if __name__ == '__main__':
    server = http.server.HTTPServer(('', PORT), Handler)
    print(f'Dashboard running on port {PORT}')
    server.serve_forever()
