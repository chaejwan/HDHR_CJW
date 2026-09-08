"""이미 본 공고를 기억해 두는 저장소 (JSON 파일 한 개)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

MAX_SEEN_PER_SITE = 3000     # 사이트별로 기억할 최대 공고 수
MAX_HISTORY_PER_SITE = 30    # 사이트별 최근 확인 기록 수
MAX_NEW_IN_HISTORY = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def empty_state() -> dict:
    return {"version": 1, "sites": {}}


def load(path: str) -> dict:
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(data, dict) or "sites" not in data:
        return empty_state()
    return data


def save(path: str, state: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def site_state(state: dict, site_id: str) -> dict:
    sites = state.setdefault("sites", {})
    return sites.setdefault(site_id, {
        "seen": {},
        "last_check": "",
        "last_success": "",
        "last_status": "",
        "last_error": "",
        "last_method": "",
        "item_count": 0,
        "fingerprint": "",
        "baseline_done": False,
        "history": [],
        "consecutive_errors": 0,
        "sample_titles": [],
        "last_new_at": "",
        "health": {"issue": "", "detail": "", "since": ""},
    })


def forget(state: dict, site_id: str) -> None:
    state.setdefault("sites", {}).pop(site_id, None)


def diff_new(entry: dict, items: list) -> list:
    """아직 본 적 없는 항목만 골라낸다."""
    seen = entry.get("seen") or {}
    return [item for item in items if item.get("id") not in seen]


def _prune_seen(entry: dict) -> None:
    seen = entry.get("seen") or {}
    if len(seen) <= MAX_SEEN_PER_SITE:
        return
    ordered = sorted(seen.items(), key=lambda kv: kv[1].get("first_seen") or "")
    for key, _value in ordered[: len(seen) - MAX_SEEN_PER_SITE]:
        seen.pop(key, None)


def remember(entry: dict, items: list, at: str) -> None:
    seen = entry.setdefault("seen", {})
    for item in items:
        seen.setdefault(item["id"], {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "date": item.get("date", ""),
            "first_seen": at,
        })
    _prune_seen(entry)


def recent_item_counts(entry: dict, limit: int = 10) -> list:
    """최근 성공한 확인들의 항목 수 (지금 것은 아직 기록 전이므로 포함되지 않는다).

    기준을 잡은 확인(baseline)보다 앞선 숫자는 쓰지 않는다. 30건짜리 메뉴를 읽다가
    14건짜리 진짜 공고를 읽게 된 것을 '급감' 으로 오해하지 않기 위해서다.
    (history 는 최신 것이 앞에 온다)
    """
    counts = []
    for record in entry.get("history") or []:
        if record.get("status") != "error":
            counts.append(int(record.get("item_count") or 0))
        if record.get("status") == "baseline" or record.get("reset"):
            break                      # 여기서부터 이전은 다른 방식으로 센 숫자다
        if len(counts) >= limit:
            break
    return counts


def record_check(entry: dict, *, at: str, status: str, new_items: list = None,
                 error: str = "", method: str = "", item_count: int = 0,
                 fingerprint: str = "", note: str = "", sample_titles: list = None,
                 reset: bool = False) -> None:
    new_items = new_items or []
    if status == "error":
        entry["consecutive_errors"] = int(entry.get("consecutive_errors") or 0) + 1
    else:
        entry["consecutive_errors"] = 0
    if new_items:
        entry["last_new_at"] = at
    if sample_titles is not None:
        entry["sample_titles"] = [t for t in sample_titles if t][:5]
    entry["last_check"] = at
    entry["last_status"] = status
    entry["last_error"] = error
    entry["last_note"] = note
    if status != "error":
        entry["last_success"] = at
        entry["last_method"] = method
        entry["item_count"] = item_count
        if fingerprint:
            entry["fingerprint"] = fingerprint
    history = entry.setdefault("history", [])
    history.insert(0, {
        "at": at,
        "status": status,
        "reset": reset,
        "new_count": len(new_items),
        "item_count": item_count,
        "error": error,
        "note": note,
        "new": [
            {"title": i.get("title", ""), "url": i.get("url", ""), "date": i.get("date", "")}
            for i in new_items[:MAX_NEW_IN_HISTORY]
        ],
    })
    del history[MAX_HISTORY_PER_SITE:]
