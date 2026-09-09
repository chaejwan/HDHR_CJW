"""메일을 언제 보낼지 정한다 (모아 보내기).

확인은 신호가 올 때마다 하되, 메일은 정해 둔 요일·시각에만 한 번 보낸다.
그 사이에 발견한 공고는 state 에 쌓아 두었다가 그때 한꺼번에 나간다.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = {"mon": "월", "tue": "화", "wed": "수", "thu": "목",
              "fri": "금", "sat": "토", "sun": "일"}


def zone(cfg: dict):
    """설정한 시간대. 이름을 못 찾으면 정해 둔 시차(utc_offset_hours)를 쓴다."""
    digest = cfg.get("digest") or {}
    name = (digest.get("timezone") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    try:
        offset = float(digest.get("utc_offset_hours"))
    except (TypeError, ValueError):
        offset = 9.0
    return timezone(timedelta(hours=offset))


def _slots(digest: dict) -> list:
    """보낼 시각들을 (시, 분) 목록으로."""
    out = []
    for raw in digest.get("times") or []:
        text = str(raw).strip()
        try:
            hour, _, minute = text.partition(":")
            out.append(time(int(hour), int(minute or 0)))
        except ValueError:
            continue
    return sorted(out) or [time(8, 0)]


def _days(digest: dict) -> set:
    names = {str(d).strip().lower()[:3] for d in (digest.get("days") or [])}
    names = {n for n in names if n in DAY_NAMES}
    return names or set(DAY_NAMES)


def last_slot(cfg: dict, now: datetime):
    """지금 기준으로 가장 최근에 지나간 발송 시각. 없으면 None."""
    digest = cfg.get("digest") or {}
    local = now.astimezone(zone(cfg))
    days, slots = _days(digest), _slots(digest)
    for back in range(0, 8):                     # 오늘부터 일주일 전까지 훑는다
        day = local.date() - timedelta(days=back)
        if DAY_NAMES[day.weekday()] not in days:
            continue
        for slot in reversed(slots):
            moment = datetime.combine(day, slot, tzinfo=local.tzinfo)
            if moment <= local:
                return moment
    return None


def next_slot(cfg: dict, now: datetime):
    """다음 발송 시각. 요일 설정이 비어 있지 않으면 항상 존재한다."""
    digest = cfg.get("digest") or {}
    local = now.astimezone(zone(cfg))
    days, slots = _days(digest), _slots(digest)
    for ahead in range(0, 8):
        day = local.date() + timedelta(days=ahead)
        if DAY_NAMES[day.weekday()] not in days:
            continue
        for slot in slots:
            moment = datetime.combine(day, slot, tzinfo=local.tzinfo)
            if moment > local:
                return moment
    return None


def should_send(cfg: dict, last_sent: datetime | None, now: datetime) -> bool:
    """지금이 '모아 둔 것을 보낼 때' 인지.

    마지막 발송 이후에 발송 시각이 지났으면 보낸다. 그래서 8시에 깨어나지
    못했더라도, 그다음 깨어나는 순간에 (하루치를 모아) 나간다.
    """
    if not (cfg.get("digest") or {}).get("enabled"):
        return True                              # 모아 보내기를 끄면 발견 즉시 보낸다
    slot = last_slot(cfg, now)
    if slot is None:
        return False
    return last_sent is None or last_sent.astimezone(slot.tzinfo) < slot


def describe(cfg: dict) -> str:
    """'월~금 08:00 (Asia/Seoul)' 처럼 사람이 읽을 문구."""
    digest = cfg.get("digest") or {}
    days = [DAY_LABELS[d] for d in DAY_NAMES if d in _days(digest)]
    times = ", ".join(slot.strftime("%H:%M") for slot in _slots(digest))
    where = (digest.get("timezone") or "").strip() or f"UTC{digest.get('utc_offset_hours', 9):+g}"
    return f"{''.join(days)} {times} ({where})"
