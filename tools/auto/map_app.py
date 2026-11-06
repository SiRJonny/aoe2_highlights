"""Map-animation webapp: serve collected positions + terrain to a canvas UI.

Serves tools/auto/map_web/ static files and, from the out folder:
    /api/data/<tag>   out/<tag>/positions.jsonl  (streamed text)
    /api/map/<tag>    out/<tag>/map.png

Usage:
    py -3.12 tools\\auto\\map_app.py [--tag demo] [--port 8787]
    -> http://localhost:8787/?tag=demo
"""
import argparse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "map_web"
OUT = ROOT / "out"

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/icons.js": ("icons.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/icons_test.html": ("icons_test.html", "text/html; charset=utf-8"),
}
ICON_DIR = WEB / "icons"


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in STATIC:
            name, ctype = STATIC[path]
            self._send((WEB / name).read_bytes(), ctype)
            return
        if path.startswith("/icons/"):
            name = Path(path).name
            f = ICON_DIR / name
            if f.exists():
                self._send(f.read_bytes(), "image/png")
                return
            self._send(b"no icon", "text/plain", 404)
            return
        if path.startswith("/api/data/"):
            tag = Path(path).name
            f = OUT / tag / "positions.jsonl"
            if not f.exists():
                self._send(b"no positions for tag", "text/plain", 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length",
                             str(f.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        if path.startswith("/api/map/"):
            tag = Path(path).name
            f = OUT / tag / "map.png"
            if not f.exists():
                self._send(b"no map for tag", "text/plain", 404)
                return
            self._send(f.read_bytes(), "image/png")
            return
        self._send(b"not found", "text/plain", 404)

    def log_message(self, fmt, *a):     # quiet
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="demo",
                    help="tag to open in the browser (any collected tag "
                         "is still servable via ?tag=...)")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}/?tag={args.tag}"
    print(f"map app: {url}  (Ctrl-C to stop)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
