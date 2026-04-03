import http.server, json, os
from pathlib import Path


BOT_DIR = Path(__file__).parent
PORT = 8080

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text):
        body = text.encode('utf-8', errors='replace')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path):
        body = open(path, 'rb').read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_404(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == '/' or self.path == '/index.html':
                self.send_html(BOT_DIR / 'dashboard.html')
            elif self.path == '/state':
                f = BOT_DIR / 'state.json'
                self.send_json(json.loads(f.read_text(encoding='utf-8')) if f.exists() else {})
            elif self.path == '/log':
                f = BOT_DIR / 'bot_log.txt'
                self.send_text(f.read_text(encoding='utf-8', errors='replace') if f.exists() else '')
            elif self.path == '/explanations':
                f = BOT_DIR / 'trade_explanations.json'
                self.send_json(json.loads(f.read_text(encoding='utf-8')) if f.exists() else [])
            elif self.path == '/shadow':
                import csv
                f = BOT_DIR / 'shadow_shorts.csv'
                if f.exists():
                    rows = []
                    with open(f, newline='', encoding='utf-8') as cf:
                        reader = csv.DictReader(cf)
                        for row in reader:
                            rows.append(dict(row))
                    self.send_json(rows)
                else:
                    self.send_json([])
            elif self.path == '/shadowlong':
                import csv
                f = BOT_DIR / 'shadow_longs.csv'
                if f.exists():
                    rows = []
                    with open(f, newline='', encoding='utf-8') as cf:
                        reader = csv.DictReader(cf)
                        for row in reader:
                            rows.append(dict(row))
                    self.send_json(rows)
                else:
                    self.send_json([])
            elif self.path == '/summary':
                f = BOT_DIR / 'summary.json'
                self.send_json(json.loads(f.read_text(encoding='utf-8')) if f.exists() else {"summary": "Waiting for first scan...", "time": ""})
            elif self.path == '/audit':
                import csv
                f = BOT_DIR / 'strategy_audit.csv'
                if f.exists():
                    rows = []
                    with open(f, newline='', encoding='utf-8') as cf:
                        reader = csv.DictReader(cf)
                        for row in reader: rows.append(dict(row))
                    self.send_json(rows)
                else:
                    self.send_json([])
            elif self.path == '/rejected':
                import csv
                f = BOT_DIR / 'rejected_signals.csv'
                if f.exists():
                    rows = []
                    with open(f, newline='', encoding='utf-8') as cf:
                        reader = csv.DictReader(cf)
                        for row in reader: rows.append(dict(row))
                    self.send_json(rows)
                else:
                    self.send_json([])
            elif self.path == '/equity':
                import csv
                f = BOT_DIR / 'equity_curve.csv'
                if f.exists():
                    rows = []
                    with open(f, newline='', encoding='utf-8') as cf:
                        reader = csv.DictReader(cf)
                        for row in reader: rows.append(dict(row))
                    self.send_json(rows)
                else:
                    self.send_json([])
            elif self.path == '/symbols':
                import csv
                f = BOT_DIR / 'symbol_performance.csv'
                if f.exists():
                    rows = []
                    with open(f, newline='', encoding='utf-8') as cf:
                        reader = csv.DictReader(cf)
                        for row in reader: rows.append(dict(row))
                    self.send_json(rows)
                else:
                    self.send_json([])
            else:
                self.send_404()
        except Exception as e:
            self.send_response(500)
            self.end_headers()

if __name__ == '__main__':
    server = http.server.HTTPServer(('', PORT), Handler)
    print(f'Dashboard server running on port {PORT}')
    server.serve_forever()
