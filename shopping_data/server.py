from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .pipeline import (
    ResultNotFound,
    ResultReadError,
    compare_published_results,
    list_published_results,
    load_current_result,
    load_run_history,
)


def serve(
    *,
    web_root: Path,
    database_path: Path,
    published_root: Path,
    port: int,
) -> None:
    web_root = web_root.resolve()
    allowed_assets = {
        "/": web_root / "index.html",
        "/index.html": web_root / "index.html",
        "/styles.css": web_root / "styles.css",
        "/app.js": web_root / "app.js",
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "ShoppingDataLoopback/0.1"

        def _json(self, status: HTTPStatus, payload: dict | list) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/api/result":
                try:
                    payload = load_current_result(database_path, published_root)
                except ResultNotFound as exc:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"data_state": "not_found", "message": str(exc)},
                    )
                except ResultReadError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"data_state": "read_error", "message": str(exc)},
                    )
                else:
                    self._json(HTTPStatus.OK, payload)
                return

            if path == "/api/runs":
                try:
                    history = load_run_history(database_path)
                except ResultReadError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"data_state": "read_error", "message": str(exc)},
                    )
                else:
                    self._json(HTTPStatus.OK, history)
                return

            if path == "/api/results":
                try:
                    results = list_published_results(database_path, published_root)
                except ResultReadError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"data_state": "read_error", "message": str(exc)},
                    )
                else:
                    self._json(HTTPStatus.OK, results)
                return

            if path == "/api/comparison":
                query = parse_qs(parsed.query)
                base_run_id = query.get("base_run_id", [None])[0]
                current_run_id = query.get("current_run_id", [None])[0]
                if not base_run_id or not current_run_id:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "data_state": "invalid_request",
                            "message": "base_run_id and current_run_id are required",
                        },
                    )
                    return
                try:
                    comparison = compare_published_results(
                        database_path,
                        published_root,
                        base_run_id=base_run_id,
                        current_run_id=current_run_id,
                    )
                except ResultNotFound as exc:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"data_state": "not_found", "message": str(exc)},
                    )
                except ResultReadError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"data_state": "read_error", "message": str(exc)},
                    )
                else:
                    self._json(HTTPStatus.OK, comparison)
                return

            asset = allowed_assets.get(path)
            if asset is None or not asset.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            body = asset.read_bytes()
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: object) -> None:
            print(f"[loopback] {self.address_string()} - {format % args}")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Shopping data result viewer: http://127.0.0.1:{port}")
    print("Only explicit UI assets and result APIs are served. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
