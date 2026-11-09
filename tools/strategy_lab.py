"""Serve the bundled, verified strategy catalogue on the local computer."""

import argparse
import json
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lisha_overlay.spoken_catalogue import SpokenCatalogue
from lisha_overlay.step_strategy import solve_steps
from lisha_overlay.strategy_catalogue import Catalogue

DEFAULT_DATA = ROOT / "src" / "lisha_overlay" / "assets" / "strategy"
MAX_REQUEST_BYTES = 20_000


def create_handler(histogram_catalogue, spoken_catalogue):
    """Bind the two policy views and static pages to a request handler."""
    directory = Path(__file__).parent
    pages = {
        "/": (directory / "step_lab.html").read_bytes(),
        "/catalogue": (directory / "strategy_lab.html").read_bytes(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *args):
            pass

        def _send_body(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, data, status=200):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send_body(payload, "application/json; charset=utf-8", status)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in pages:
                self._send_body(pages[parsed.path], "text/html; charset=utf-8")
                return
            if parsed.path not in ("/api/summary", "/api/solve", "/api/example"):
                self.send_error(404)
                return

            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                policy = query.get("policy", ["bulk"])[0]
                if policy not in ("bulk", "buff"):
                    raise ValueError("指挥方式必须为 bulk 或 buff。")
                active = (
                    histogram_catalogue if policy == "buff" else spoken_catalogue
                )
                if parsed.path == "/api/summary":
                    result = active.summary
                else:
                    mode = query.get("mode", ["all"])[0]
                    if mode not in ("all", "circle"):
                        raise ValueError("模式必须为 all 或 circle。")
                    if parsed.path == "/api/example":
                        family = int(query.get("family", ["0"])[0])
                        indices = np.flatnonzero(
                            active.tables[mode]["family"] == family
                        )
                        if not len(indices):
                            raise ValueError("当前模式没有这一类。")
                        selected = int(random.choice(indices))
                        counts = active.counts[selected].tolist()
                    else:
                        counts = [
                            int(value)
                            for value in query.get("counts", [""])[0].split(",")
                        ]
                    result = active.solve(counts, mode)
                self._send_json(result)
            except (ValueError, KeyError, TypeError, IndexError) as error:
                self._send_json({"error": str(error)}, 400)

        def do_POST(self):
            if urlparse(self.path).path != "/api/steps":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST_BYTES:
                    raise ValueError("请求大小无效。")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("请求格式错误。")
                mode = request.get("mode", "all")
                if mode not in ("all", "circle"):
                    raise ValueError("模式必须为 all 或 circle。")
                result = solve_steps(request.get("roster"), mode, spoken_catalogue)
                self._send_json(result)
            except (ValueError, KeyError, TypeError, IndexError) as error:
                self._send_json({"error": str(error)}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--port", type=int, default=18776)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")

    histogram_catalogue = Catalogue(args.data)
    spoken_catalogue = SpokenCatalogue(histogram_catalogue, args.data)
    handler = create_handler(histogram_catalogue, spoken_catalogue)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
