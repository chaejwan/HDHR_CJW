"""페이지 내려받기. 표준 라이브러리만 사용한다."""

from __future__ import annotations

import gzip
import io
import re
import time
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

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# 상대 서버가 일시적으로 느리거나 막을 때를 대비한 재시도 (사이트 한 곳당)
RETRY_ATTEMPTS = 3
RETRY_WAIT_SEC = (3, 8)


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


def fetch(url: str, timeout: float = 20, user_agent: str = DEFAULT_UA, headers: dict | None = None,
          method: str = "GET", body: str = "") -> FetchResult:
    """주소 하나를 받아 온다. 응답이 없거나 일시적 오류면 몇 번 더 시도한다."""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return _fetch_once(url, timeout=timeout, user_agent=user_agent, headers=headers,
                               method=method, body=body)
        except FetchError as exc:
            last_error = exc
            if not exc.retryable or attempt == RETRY_ATTEMPTS:
                break
            time.sleep(RETRY_WAIT_SEC[min(attempt - 1, len(RETRY_WAIT_SEC) - 1)])
    message = str(last_error)
    if last_error is not None and last_error.retryable and RETRY_ATTEMPTS > 1:
        message = f"{message} ({RETRY_ATTEMPTS}회 시도)"
    raise FetchError(message, retryable=bool(last_error and last_error.retryable))


def _fetch_once(url: str, timeout: float = 20, user_agent: str = DEFAULT_UA, headers: dict | None = None,
                method: str = "GET", body: str = "") -> FetchResult:
    """실제로 한 번 요청한다. body 를 주면 POST 로 보낸다 (검색형 API 용)."""
    req_headers = {
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    method = (method or "GET").upper()
    data = None
    if body:
        data = body.encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
        if method == "GET":
            method = "POST"
    req_headers.update(headers or {})
    req = urlrequest.Request(url, data=data, headers=req_headers, method=method)
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
        # 429(요청 과다)와 5xx 는 잠시 뒤 다시 해 볼 만하다
        retryable = exc.code == 429 or 500 <= exc.code < 600
        raise FetchError(f"HTTP {exc.code} {exc.reason}", retryable=retryable) from exc
    except URLError as exc:
        reason = exc.reason
        text = str(reason)
        if isinstance(reason, TimeoutError) or "timed out" in text:
            raise FetchError(f"응답 시간 초과 ({timeout:g}초)", retryable=True) from exc
        raise FetchError(f"접속 실패: {text}", retryable=True) from exc
    except TimeoutError as exc:
        raise FetchError(f"응답 시간 초과 ({timeout:g}초)", retryable=True) from exc
    except OSError as exc:
        raise FetchError(f"접속 실패: {exc}", retryable=True) from exc


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
                try:
                    # 스크롤해야 목록을 더 불러오는 사이트 대응
                    for _ in range(3):
                        page.mouse.wheel(0, 4000)
                        page.wait_for_timeout(800)
                except Exception:
                    pass
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
        try:
            request = resp.request
            method = request.method
            post_data = request.post_data or ""
        except Exception:
            method, post_data = "GET", ""
        # 확장자만 보고 확실한 정적 파일은 건너뛰고, 나머지는 본문이 JSON 인지로 판단한다.
        if url.split("?")[0].endswith(
            (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
             ".woff", ".woff2", ".ttf", ".ico", ".mp4", ".webm")
        ):
            continue
        if "text/html" in content_type:
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
                    "content_type": content_type, "text": body,
                    "method": method, "post_data": post_data[:2000]})
        if len(out) >= MAX_CAPTURED:
            break
    return out
