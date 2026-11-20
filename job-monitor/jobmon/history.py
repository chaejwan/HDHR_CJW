"""공고 이력을 깃허브 저장소 안에서 볼 수 있는 표(마크다운)로 만든다.

사내망은 chaejwan.github.io(설정 화면)를 막아 두었지만 github.com 은 열려
있다. 그래서 확인할 때마다 data/HISTORY.md 를 새로 써 두고, github.com 의
저장소 화면에서 표로 보이게 한다. 설정 화면 '채용 공고 이력' 과 같은
목록(state.json 의 archive)을 같은 순서·같은 칸으로 보여 준다.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone

from . import digest as digest_mod
from . import store as store_mod

FILE_NAME = "HISTORY.md"

# 마크다운에서 뜻을 갖는 글자들. 사이트에서 가져온 제목에 섞여 있어도 글자 그대로 보이게 한다.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>|#!~&])")


def _text(value) -> str:
    """표 한 칸에 들어갈 글자. 줄바꿈을 없애고 마크다운 기호는 글자로 바꾼다."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return _MD_SPECIAL.sub(r"\\\1", text)


def _target(url) -> str:
    """링크 주소. http(s) 만 쓰고, 표나 링크를 깨뜨리는 글자는 %표기로 바꾼다."""
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return ""
    for char, code in ((" ", "%20"), ("(", "%28"), (")", "%29"), ("<", "%3C"),
                       (">", "%3E"), ("|", "%7C"), ("\\", "%5C")):
        url = url.replace(char, code)
    return url


def _deadline(value) -> str:
    """설정 화면(app.js fmtDeadline)과 같은 규칙으로 마감 날짜를 다듬는다."""
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return re.sub(r"-\s", " ", text.replace(".", "-"))


def _when(iso, tz) -> str:
    try:
        moment = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def render(state: dict, cfg: dict, now: datetime = None) -> str:
    tz = digest_mod.zone(cfg)
    names = {site.get("id"): site.get("name") for site in (cfg.get("sites") or [])}
    rows = []
    for row in store_mod.archive(state):
        rows.append(dict(row, site_name=names.get(row.get("site_id"))
                         or row.get("site_name") or row.get("site_id") or ""))
    rows.sort(key=lambda row: str(row.get("found_at") or ""), reverse=True)

    now = now or datetime.now(timezone.utc)
    lines = [
        "<!-- 자동으로 만들어지는 파일입니다. 확인할 때마다 새로 쓰므로 직접 고치지 마세요. -->",
        "",
        "# 채용 공고 이력",
        "",
        f"마지막 갱신 **{_when(now.isoformat(), tz)}** · "
        f"최근 발견한 공고 **{len(rows)}건** (최신 순, 최대 {store_mod.MAX_ARCHIVE}건)",
        "",
    ]
    counts = {}
    for row in rows:
        counts[row["site_name"]] = counts.get(row["site_name"], 0) + 1
    if counts:
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        lines.append(" · ".join(f"{_text(name)} {count}" for name, count in ordered))
        lines.append("")
    page_url = (cfg.get("page_url") or "").strip()
    if _target(page_url):
        lines.append(f"> 회사 밖에서는 [채용공고 모니터]({_target(page_url)}) 에서 "
                     "회사별 보기와 검색을 쓸 수 있습니다.")
        lines.append("")

    if not rows:
        lines.append("아직 발견한 공고가 없습니다.")
        return "\n".join(lines) + "\n"

    lines.append("| 회사 | 제목 | 마감 | 발견 |")
    lines.append("|:---:|:---|:---:|:---:|")
    for row in rows:
        title = _text(row.get("title")) or "(제목 없음)"
        link = _target(row.get("url"))
        cell = f"[{title}]({link})" if link else title
        lines.append(f"| {_text(row['site_name'])} | {cell} | "
                     f"{_text(_deadline(row.get('date'))) or '—'} | "
                     f"{_when(row.get('found_at'), tz) or '—'} |")
    return "\n".join(lines) + "\n"


def write(path: str, state: dict, cfg: dict, now: datetime = None) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".history-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render(state, cfg, now))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
