"""페이지 내려받기. 표준 라이브러리만 사용한다."""

from __future__ import annotations

import gzip
import io
import re
import zlib
from dataclasses import dataclass, field
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class FetchError(Exception):
    """접속 실패. 메시지는 사용자에게 그대로 보여 준다."""


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    content_type: str = ""
    rendered: bool = False
    headers: dict = field(default_factory=dict)

    @property
    def looks_json(self) -> bool:
        if "json" in (self.content_type or ""):
            return True
        head = self.text.lstrip()[:1]
        return head in ("{", "[")


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if encoding == "gzip":
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except OSError:
        return raw
    return raw


def _charset(content_type: str, raw: bytes) -> str:
    match = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if match:
        return match.group(1)
    head = raw[:4096].decode("ascii", "ignore")
    match = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    if match:
        return match.group(1)
    return "utf-8"


def fetch(url: str, timeout: float = 20, user_agent: str = DEFAULT_UA, headers: dict | None = None) -> FetchResult:
    req_headers = {
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    req_headers.update(headers or {})
    req = urlrequest.Request(url, headers=req_headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            info = resp.headers
            content_type = info.get("Content-Type", "")
            raw = _decompress(raw, info.get("Content-Encoding", ""))
            text = raw.decode(_charset(content_type, raw), "replace")
            return FetchResult(
                url=resp.geturl(),
                status=getattr(resp, "status", 200) or 200,
                text=text,
                content_type=content_type,
                headers={k.lower(): v for k, v in info.items()},
            )
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise FetchError(f"접속 실패: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError(f"응답 시간 초과 ({timeout}초)") from exc
    except OSError as exc:
        raise FetchError(f"접속 실패: {exc}") from exc


def browser_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def fetch_rendered(url: str, timeout: float = 30, user_agent: str = DEFAULT_UA,
                   wait_ms: int = 2500, capture: bool = False):
    """자바스크립트로 목록을 그리는 사이트용. Playwright 가 설치돼 있어야 한다.

    capture=True 면 (FetchResult, 페이지가 주고받은 JSON 응답 목록) 을 함께 돌려준다.
    사이트가 어떤 API 로 공고 목록을 가져오는지 찾을 때 쓴다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 설치 환경에 따라 다름
        raise FetchError(
            "browser 모드에는 Playwright 가 필요합니다. "
            "pip install playwright && playwright install chromium 을 실행하세요."
        ) from exc

    captured = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_context(user_agent=user_agent or DEFAULT_UA).new_page()
                responses = []
                if capture:
                    page.on("response", lambda resp: responses.append(resp))
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                except Exception:
                    pass
                page.wait_for_timeout(wait_ms)
                html = page.content()
                final_url = page.url
                if capture:
                    captured = _read_json_responses(responses)
            finally:
                browser.close()
    except Exception as exc:  # pragma: no cover - 브라우저 실행 실패
        raise FetchError(f"브라우저 렌더링 실패: {exc}") from exc

    result = FetchResult(url=final_url, status=200, text=html, content_type="text/html", rendered=True)
    return (result, captured) if capture else result


MAX_CAPTURED = 60
MAX_CAPTURED_BYTES = 3_000_000


def _read_json_responses(responses) -> list:
    """렌더링 중 오간 응답 가운데 JSON 으로 보이는 것만 본문까지 읽어 둔다."""
    out = []
    for resp in responses[:200]:
        try:
            content_type = (resp.header_value("content-type") or "").lower()
        except Exception:
            content_type = ""
        url = getattr(resp, "url", "")
        if "json" not in content_type and not any(
            hint in url.lower() for hint in ("/api/", "/rest/", ".json", "recruit", "notice", "list")
        ):
            continue
        if url.endswith((".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ico")):
            continue
        try:
            body = resp.text()
        except Exception:
            continue
        if not body or len(body) > MAX_CAPTURED_BYTES:
            continue
        head = body.lstrip()[:1]
        if head not in ("{", "["):
            continue
        out.append({"url": url, "status": getattr(resp, "status", 0),
                    "content_type": content_type, "text": body})
        if len(out) >= MAX_CAPTURED:
            break
    return out
