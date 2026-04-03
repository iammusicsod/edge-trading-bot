import http.server, json, os
from pathlib import Path

BOT_DIR = Path(__file__).parent
PORT = 8080

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data):
        try:
            body = json.dumps(data, ensure_ascii=True).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error_response(str(e))

    def send_text(self, text):
        try:
            body = text.encode('utf-8', errors='replace')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error_response(str(e))

    def send_html(self, path):
        try:
            body = open(path, 'rb').read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error_response(str(e))

    def send_error_response(self, msg=''):
        try:
            body = json.dumps({'error': msg}).encode('utf-8')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except:
            pass

    def read_json_file(self, filepath, default):
        try:
            if filepath.exists():
                raw = filepath.read_bytes()
                text = raw.decode('utf-8', errors='replace')
                return json.loads(text)
            return default
        except Exception as e:
            return default

    def read_csv_file(self, filepath):
        import csv
        try:
            if not filepath.exists():
                return []
            rows = []
            with open(filepath, newline='', encoding='utf-8', errors='replace') as cf:
                reader = csv.DictReader(cf)
                for row in reader:
                    rows.append(dict(row))
            return rows
        except Exception as e:
            return []

    def do_GET(self):
        try:
            if self.path == '/' or self.path == '/index.html':
                self.send_html(BOT_DIR / 'dashboard.html')

            elif self.path == '/state':
                data = self.read_json_file(BOT_DIR / 'state.json', {})
                self.send_json(data)

            elif self.path == '/summary':
                data = self.read_json_file(BOT_DIR / 'summary.json', {"summary": "Waiting for first scan...", "time": "", "signals": {}})
                self.send_json(data)

            elif self.path == '/log':
                f = BOT_DIR / 'bot_log.txt'
                try:
                    text = f.read_bytes().decode('utf-8', errors='replace') if f.exists() else ''
                except:
                    text = ''
                self.send_text(text)

            elif self.path == '/explanations':
                data = self.read_json_file(BOT_DIR / 'trade_explanations.json', [])
                self.send_json(data)

            elif self.path == '/shadow':
                rows = self.read_csv_file(BOT_DIR / 'shadow_shorts.csv')
                self.send_json(rows)

            elif self.path == '/shadowlong':
                rows = self.read_csv_file(BOT_DIR / 'shadow_longs.csv')
                self.send_json(rows)

            elif self.path == '/audit':
                rows = self.read_csv_file(BOT_DIR / 'strategy_audit.csv')
                self.send_json(rows)

            elif self.path == '/rejected':
                rows = self.read_csv_file(BOT_DIR / 'rejected_signals.csv')
                self.send_json(rows)

            elif self.path == '/equity':
                rows = self.read_csv_file(BOT_DIR / 'equity_curve.csv')
                self.send_json(rows)

            elif self.path == '/symbols':
                rows = self.read_csv_file(BOT_DIR / 'symbol_performance.csv')
                self.send_json(rows)

            else:
                self.send_response(404)
                self.end_headers()

        except Exception as e:
            self.send_error_response(str(e))


if __name__ == '__main__':
    server = http.server.HTTPServer(('', PORT), Handler)
    print(f'Dashboard server running on port {PORT}')
    server.serve_forever()
