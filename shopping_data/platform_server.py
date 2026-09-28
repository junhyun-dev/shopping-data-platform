from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import PLATFORM_FIXTURE_MANIFEST, WEB_ROOT
from .pipeline import PipelineBusy
from .platform import (
    PlatformFailure,
    PlatformNotFound,
    PlatformReadError,
    load_platform_result,
    load_platform_state,
    platform_runtime_paths,
    recover_platform_segment,
)


MAX_WRITE_BODY = 4_096


def make_platform_server(
    *, runtime_root: Path, manifest_path: Path = PLATFORM_FIXTURE_MANIFEST, port: int
) -> ThreadingHTTPServer:
    database_path, published_root = platform_runtime_paths(runtime_root)
    web_root = WEB_ROOT.resolve()
    allowed_assets = {
        "/": web_root / "platform.html",
        "/platform.html": web_root / "platform.html",
        "/platform.css": web_root / "platform.css",
        "/platform.js": web_root / "platform.js",
        "/platform-preview.html": web_root / "platform-preview.html",
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "ShoppingPlatformLoopback/0.1"

        def _headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")

        def _json(self, status: HTTPStatus, payload: dict | list) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._headers()
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _error(self, status: HTTPStatus, state: str, message: str) -> None:
            self._json(status, {"data_state": state, "message": message})

        def _valid_same_origin(self) -> bool:
            host = self.headers.get("Host", "")
            hostname = host.rsplit(":", 1)[0]
            if hostname not in {"127.0.0.1", "localhost"}:
                return False
            origin = self.headers.get("Origin")
            if origin != f"http://{host}":
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site")
            return fetch_site in {None, "same-origin"}

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/api/platform/state":
                if parsed.query:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "state query parameters are not supported")
                    return
                try:
                    payload = load_platform_state(
                        manifest_path=manifest_path,
                        database_path=database_path,
                        published_root=published_root,
                    )
                except PlatformNotFound as exc:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", str(exc))
                except PlatformReadError as exc:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "read_error", str(exc))
                else:
                    self._json(HTTPStatus.OK, payload)
                return

            if parsed.path == "/api/platform/result":
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) != {"run_id"} or len(query["run_id"]) != 1 or not query["run_id"][0]:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "one non-empty run_id is required")
                    return
                try:
                    payload = load_platform_result(database_path, published_root, query["run_id"][0])
                except PlatformNotFound as exc:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", str(exc))
                except PlatformReadError as exc:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "read_error", str(exc))
                else:
                    self._json(HTTPStatus.OK, payload)
                return

            asset = allowed_assets.get(parsed.path)
            if parsed.query or asset is None or not asset.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = asset.read_bytes()
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._headers()
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/platform/recover" or urlsplit(self.path).query:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not self._valid_same_origin():
                self._error(HTTPStatus.FORBIDDEN, "origin_rejected", "a same-origin loopback request is required")
                return
            if self.headers.get_content_type() != "application/json":
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "invalid_request", "application/json is required")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_WRITE_BODY:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid_request", "request body is missing or too large")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "request body must be valid UTF-8 JSON")
                return
            if not isinstance(payload, dict) or set(payload) != {"target_date", "segment_id"}:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "target_date and segment_id are required")
                return
            if not all(isinstance(payload[key], str) for key in payload):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "recovery fields must be text")
                return
            try:
                result = recover_platform_segment(
                    manifest_path=manifest_path,
                    database_path=database_path,
                    published_root=published_root,
                    target_date=payload["target_date"],
                    segment_id=payload["segment_id"],
                )
            except PipelineBusy as exc:
                self._error(HTTPStatus.CONFLICT, "busy", str(exc))
            except PlatformFailure as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "processing_failed", str(exc))
            except (PlatformNotFound, PlatformReadError) as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "read_error", str(exc))
            else:
                self._json(HTTPStatus.OK, {"status": "succeeded", "run_id": result["run"]["run_id"]})

        def log_message(self, format: str, *args: object) -> None:
            print(f"[platform-loopback] {self.address_string()} - {format % args}")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_platform(
    *, runtime_root: Path, manifest_path: Path = PLATFORM_FIXTURE_MANIFEST, port: int
) -> None:
    server = make_platform_server(runtime_root=runtime_root, manifest_path=manifest_path, port=port)
    print(f"Synthetic shopping platform: http://127.0.0.1:{port}")
    print("Only explicit UI assets and allowlisted platform APIs are served. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
