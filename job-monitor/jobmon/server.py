"""설정용 웹 UI + 간단한 JSON API (표준 라이브러리 http.server)."""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config as config_mod
from . import fetch as fetch_mod
from . import notify as notify_mod
from . import store as store_mod

WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
PASSWORD_CLEAR = "__CLEAR__"
MAX_BODY_BYTES = 2_000_000


def _public_config(cfg: dict) -> dict:
    """비밀번호는 화면으로 내보내지 않는다."""
    data = json.loads(json.dumps(cfg))
    email = data.get("email") or {}
    email["password_saved"] = bool((cfg.get("email") or {}).get("password"))
    email["password"] = ""
    env_name = (email.get("password_env") or "").strip()
    email["password_env_set"] = bool(env_name and os.environ.get(env_name))
    return data


def _merge_password(new_cfg: dict, old_cfg: dict) -> dict:
    """화면에서 빈 비밀번호가 오면 기존 값을 유지한다."""
    new_email = new_cfg.setdefault("email", {})
    old_password = (old_cfg.get("email") or {}).get("password") or ""
    incoming = new_email.get("password")
    if incoming == PASSWORD_CLEAR:
        new_email["password"] = ""
    elif not incoming:
        new_email["password"] = old_password
    new_email.pop("password_saved", None)
    new_email.pop("password_env_set", None)
    return new_cfg


def _state_view(monitor, cfg: dict, state: dict) -> dict:
    sites = []
    now = datetime.now().astimezone()
    for site in cfg.get("sites") or []:
        entry = (state.get("sites") or {}).get(site["id"]) or {}
        due = monitor.next_due(cfg, state, site)
        history = entry.get("history") or []
        recent_new = []
        for record in history:
            for item in record.get("new") or []:
                recent_new.append(dict(item, at=record.get("at", "")))
        sites.append({
            "id": site["id"],
            "name": site.get("name"),
            "url": site.get("url"),
            "enabled": site.get("enabled", True),
            "interval_hours": config_mod.interval_hours(cfg, site),
            "last_check": entry.get("last_check", ""),
            "last_status": entry.get("last_status", ""),
            "last_error": entry.get("last_error", ""),
            "last_note": entry.get("last_note", ""),
            "last_method": entry.get("last_method", ""),
            "item_count": entry.get("item_count", 0),
            "seen_count": len(entry.get("seen") or {}),
            "next_due": due.isoformat(timespec="seconds"),
            "overdue": due <= now,
            "recent_new": recent_new[:20],
            "history": [
                {k: v for k, v in record.items() if k != "new"} for record in history[:10]
            ],
        })
    return {
        "now": store_mod.now_iso(),
        "scheduler_tick": monitor.last_scheduler_tick,
        "browser_available": fetch_mod.browser_available(),
        "sites": sites,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JobMonitor/1.0"
    monitor = None  # ServerFactory 에서 주입

    # ------------------------------------------------------------ 도우미
    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400):
        self._json({"ok": False, "error": message}, status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("요청 본문이 너무 큽니다.")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"잘못된 JSON 입니다: {exc}") from exc

    def _same_origin(self) -> bool:
        """다른 사이트에서 이 로컬 서버로 요청을 보내는 것을 막는다."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == (self.headers.get("Host") or "")

    def log_message(self, fmt, *args):  # 접속 로그는 조용히
        pass

    # ------------------------------------------------------------ 정적 파일
    def _serve_static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        rel = posixpath.normpath(path).lstrip("/")
        full = os.path.join(WEBUI_DIR, rel)
        if not os.path.abspath(full).startswith(WEBUI_DIR) or not os.path.isfile(full):
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), content_type)

    # ------------------------------------------------------------ 라우팅
    def do_GET(self):
        path = urlparse(self.path).path
        monitor = self.monitor
        if path == "/api/config":
            cfg = monitor.load_config()
            self._json({
                "ok": True,
                "config": _public_config(cfg),
                "warnings": config_mod.validate(cfg),
                "config_path": monitor.config_path,
                "browser_available": fetch_mod.browser_available(),
            })
        elif path == "/api/state":
            cfg = monitor.load_config()
            self._json({"ok": True, "state": _state_view(monitor, cfg, monitor.load_state())})
        elif path == "/api/log":
            self._json({"ok": True, "lines": monitor.read_log(200)})
        elif path.startswith("/api/"):
            self._error("없는 API 입니다.", 404)
        else:
            self._serve_static(path)

    do_HEAD = do_GET

    def do_PUT(self):
        self.do_POST()

    def do_POST(self):
        path = urlparse(self.path).path
        monitor = self.monitor
        if not path.startswith("/api/"):
            self._error("없는 주소입니다.", 404)
            return
        if not self._same_origin():
            self._error("허용되지 않은 요청입니다.", 403)
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._error(str(exc))
            return

        try:
            if path == "/api/config":
                old = monitor.load_config()
                merged = _merge_password(payload.get("config") or {}, old)
                saved = monitor.save_config(merged)
                monitor.log("설정을 저장했습니다.")
                self._json({
                    "ok": True,
                    "config": _public_config(saved),
                    "warnings": config_mod.validate(saved),
                })
            elif path == "/api/check":
                summary = monitor.run_check(
                    site_ids=payload.get("site_ids") or None,
                    force=bool(payload.get("force")),
                    notify=payload.get("notify", True),
                )
                self._json({"ok": True, "summary": summary})
            elif path == "/api/preview":
                site = payload.get("site") or {}
                self._json({"ok": True, "preview": monitor.preview(site)})
            elif path == "/api/test-email":
                cfg = monitor.load_config()
                email_cfg = dict(cfg.get("email") or {})
                recipients = payload.get("recipients") or cfg.get("recipients") or []
                try:
                    notify_mod.send_test(email_cfg, recipients)
                except notify_mod.NotifyError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                monitor.log(f"테스트 메일 발송 완료 → {', '.join(recipients)}")
                self._json({"ok": True, "message": f"{', '.join(recipients)} 로 테스트 메일을 보냈습니다."})
            elif path == "/api/reset":
                site_id = payload.get("site_id")
                state = monitor.load_state()
                if site_id:
                    store_mod.forget(state, site_id)
                else:
                    state = store_mod.empty_state()
                monitor.save_state(state)
                monitor.log(f"기록 초기화: {site_id or '전체'}")
                self._json({"ok": True})
            else:
                self._error("없는 API 입니다.", 404)
        except Exception as exc:  # 서버가 죽지 않도록 방어
            monitor.log(f"API 오류 ({path}): {exc}")
            self._error(f"처리 중 오류가 발생했습니다: {exc}", 500)


def serve(monitor, host: str = "127.0.0.1", port: int = 8765, with_scheduler: bool = True):
    handler = type("BoundHandler", (Handler,), {"monitor": monitor})
    httpd = ThreadingHTTPServer((host, port), handler)
    if with_scheduler:
        monitor.start_scheduler()
    shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"\n  채용공고 모니터 설정 화면:  http://{shown_host}:{port}\n"
          f"  설정 파일: {monitor.config_path}\n"
          f"  종료하려면 Ctrl+C\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.", flush=True)
    finally:
        monitor.stop_scheduler()
        httpd.server_close()
