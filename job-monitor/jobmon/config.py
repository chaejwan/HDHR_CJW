"""설정 파일 읽기/쓰기.

설정은 사람이 직접 열어 고칠 수 있도록 JSON 한 벌로 관리한다.
웹 UI 에서 저장할 때도 같은 파일을 쓴다.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid

# 확인 주기 하한/상한 (시간). 너무 잦은 접속은 상대 서버에 부담이 된다.
MIN_INTERVAL_HOURS = 0.1
MAX_INTERVAL_HOURS = 24 * 30

DEFAULT_CONFIG = {
    "check_interval_hours": 24,
    "notify_on_first_run": False,
    "recipients": [],
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "security": "starttls",
        "username": "",
        "password": "",
        "password_env": "JOBMON_SMTP_PASSWORD",
        "from_addr": "",
        "subject_prefix": "[채용 알림]",
    },
    "request": {
        "timeout_sec": 20,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    },
    "sites": [],
}

DEFAULT_SITE = {
    "id": "",
    "name": "",
    "url": "",
    "enabled": True,
    # auto | html | json | browser
    #  auto    : JSON-LD → 내장 JSON → <a> 링크 → 본문 해시 순서로 자동 탐지
    #  html    : <a> 링크만 사용
    #  json    : url 이 JSON API 인 경우 (json 항목의 매핑 사용)
    #  browser : Playwright 로 렌더링한 뒤 auto 와 동일하게 추출 (SPA 사이트용)
    "mode": "auto",
    "interval_hours": None,       # None 이면 전역 주기 사용
    "url_pattern": "",            # 링크 주소 필터 (정규식)
    "title_pattern": "",          # 제목 필터 (정규식)
    "exclude_pattern": "",        # 제외 필터 (정규식, 주소·제목 모두에 적용)
    "max_items": 300,             # 한 번에 인정할 최대 항목 수 (노이즈 방지)
    "json": {                     # mode == "json" 일 때만 사용
        "items_path": "",
        "id_field": "",
        "title_field": "",
        "url_field": "",
        "url_template": "",
        "date_field": "",
    },
    "note": "",
}

# 처음 실행할 때 넣어 주는 예시 사이트.
STARTER_SITES = [
    {
        "id": "hanwhain",
        "name": "한화 채용 (hanwhain)",
        "url": "https://www.hanwhain.com/portal/apply/recruit",
        "mode": "auto",
        "note": "자바스크립트로 목록을 그리는 사이트라 결과가 비면 mode 를 browser 로 바꾸세요.",
    },
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:40]


def new_site_id(name: str, url: str, taken: set[str]) -> str:
    base = _slugify(name) or _slugify(re.sub(r"^https?://(www\.)?", "", url or "")) or "site"
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
        if n > 999:
            candidate = f"{base}-{uuid.uuid4().hex[:6]}"
            break
    return candidate


def _merge_defaults(value, defaults):
    """defaults 에 있는 키를 채워 넣은 사본을 돌려준다."""
    out = copy.deepcopy(defaults)
    if isinstance(value, dict):
        for key, val in value.items():
            if key in out and isinstance(out[key], dict) and isinstance(val, dict):
                out[key] = _merge_defaults(val, out[key])
            else:
                out[key] = val
    return out


def _clamp_interval(value, fallback):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if num <= 0:
        return fallback
    num = max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, num))
    return int(num) if float(num).is_integer() else round(num, 2)


def normalize(config: dict) -> dict:
    """빠진 키를 채우고 값을 정리한다. 잘못된 값은 기본값으로 되돌린다."""
    cfg = _merge_defaults(config or {}, DEFAULT_CONFIG)
    cfg["check_interval_hours"] = _clamp_interval(
        cfg.get("check_interval_hours"), DEFAULT_CONFIG["check_interval_hours"]
    )
    cfg["notify_on_first_run"] = bool(cfg.get("notify_on_first_run"))

    recipients = cfg.get("recipients") or []
    if isinstance(recipients, str):
        recipients = re.split(r"[,\s;]+", recipients)
    cfg["recipients"] = [addr.strip() for addr in recipients if isinstance(addr, str) and addr.strip()]

    email = cfg["email"]
    email["enabled"] = bool(email.get("enabled"))
    try:
        email["smtp_port"] = int(email.get("smtp_port") or 587)
    except (TypeError, ValueError):
        email["smtp_port"] = 587
    if email.get("security") not in ("starttls", "ssl", "none"):
        email["security"] = "starttls"

    req = cfg["request"]
    try:
        req["timeout_sec"] = max(3, min(120, int(req.get("timeout_sec") or 20)))
    except (TypeError, ValueError):
        req["timeout_sec"] = 20

    sites = []
    taken: set[str] = set()
    for raw in cfg.get("sites") or []:
        if not isinstance(raw, dict):
            continue
        site = _merge_defaults(raw, DEFAULT_SITE)
        site["url"] = (site.get("url") or "").strip()
        if not site["url"]:
            continue
        if not re.match(r"^https?://", site["url"]):
            site["url"] = "https://" + site["url"]
        site["name"] = (site.get("name") or site["url"]).strip()
        site["enabled"] = bool(site.get("enabled", True))
        if site.get("mode") not in ("auto", "html", "json", "browser"):
            site["mode"] = "auto"
        if site.get("interval_hours") in (None, "", 0):
            site["interval_hours"] = None
        else:
            site["interval_hours"] = _clamp_interval(site["interval_hours"], None)
        try:
            site["max_items"] = max(1, min(2000, int(site.get("max_items") or 300)))
        except (TypeError, ValueError):
            site["max_items"] = 300
        site_id = (site.get("id") or "").strip()
        if not site_id or site_id in taken:
            site_id = new_site_id(site["name"], site["url"], taken)
        site["id"] = site_id
        taken.add(site_id)
        sites.append(site)
    cfg["sites"] = sites
    return cfg


def validate(config: dict) -> list[str]:
    """저장 전에 사용자에게 보여 줄 경고 문구를 만든다 (저장을 막지는 않는다)."""
    warnings: list[str] = []
    email = config.get("email") or {}
    if email.get("enabled"):
        if not config.get("recipients"):
            warnings.append("메일 발송이 켜져 있지만 수신 이메일이 비어 있습니다.")
        if not email.get("smtp_host"):
            warnings.append("SMTP 서버 주소가 비어 있습니다.")
        if not email.get("password") and not os.environ.get(email.get("password_env") or ""):
            warnings.append(
                "SMTP 비밀번호가 비어 있습니다. 설정에 직접 입력하거나 환경변수 "
                f"{email.get('password_env') or 'JOBMON_SMTP_PASSWORD'} 로 지정하세요."
            )
    for site in config.get("sites") or []:
        for key in ("url_pattern", "title_pattern", "exclude_pattern"):
            pattern = site.get(key) or ""
            if pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    warnings.append(f"[{site.get('name')}] {key} 정규식 오류: {exc}")
    if not config.get("sites"):
        warnings.append("확인할 사이트가 없습니다.")
    return warnings


def load(path: str, create_if_missing: bool = True) -> dict:
    if not os.path.exists(path):
        cfg = normalize(copy.deepcopy(DEFAULT_CONFIG))
        cfg["sites"] = normalize({"sites": STARTER_SITES})["sites"]
        if create_if_missing:
            save(path, cfg)
        return cfg
    with open(path, "r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"설정 파일을 읽을 수 없습니다 ({path}): {exc}") from exc
    return normalize(raw)


def save(path: str, config: dict) -> dict:
    """원자적으로 저장한다. 비밀번호가 들어갈 수 있으므로 권한은 600 으로."""
    cfg = normalize(config)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return cfg


# ---------------------------------------------------------------- 환경변수 우선 적용
# GitHub Actions 등에서 비밀 정보를 저장소 파일 대신 시크릿(환경변수)으로 넘길 때 쓴다.
# 여기서 덮어쓴 값은 설정 파일에 다시 저장되지 않는다.
ENV_OVERRIDES = {
    "JOBMON_SMTP_HOST": ("email", "smtp_host"),
    "JOBMON_SMTP_PORT": ("email", "smtp_port"),
    "JOBMON_SMTP_SECURITY": ("email", "security"),
    "JOBMON_SMTP_USER": ("email", "username"),
    "JOBMON_SMTP_FROM": ("email", "from_addr"),
}


def effective(config: dict) -> dict:
    """환경변수로 넘어온 값을 얹은 사본을 돌려준다 (메일 발송 직전에 사용)."""
    cfg = copy.deepcopy(config)
    for env_name, (section, key) in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value not in (None, ""):
            cfg.setdefault(section, {})[key] = value
    recipients = os.environ.get("JOBMON_RECIPIENTS")
    if recipients:
        cfg["recipients"] = [a.strip() for a in re.split(r"[,\s;]+", recipients) if a.strip()]
    enabled = os.environ.get("JOBMON_EMAIL_ENABLED")
    if enabled not in (None, ""):
        cfg.setdefault("email", {})["enabled"] = enabled.strip().lower() in ("1", "true", "yes", "on")
    elif os.environ.get("JOBMON_SMTP_HOST"):
        # 보내는 서버를 시크릿으로 지정했다면 별도 설정 없이도 메일을 보낸다.
        cfg.setdefault("email", {})["enabled"] = True
    return normalize(cfg)


def env_summary() -> dict:
    """어떤 값이 환경변수로 채워져 있는지 (값은 노출하지 않는다)."""
    names = list(ENV_OVERRIDES) + ["JOBMON_RECIPIENTS", "JOBMON_EMAIL_ENABLED", "JOBMON_SMTP_PASSWORD"]
    return {name: bool(os.environ.get(name)) for name in names}


def interval_hours(config: dict, site: dict) -> float:
    return float(site.get("interval_hours") or config.get("check_interval_hours") or 24)


def find_site(config: dict, site_id: str) -> dict | None:
    for site in config.get("sites") or []:
        if site["id"] == site_id:
            return site
    return None
