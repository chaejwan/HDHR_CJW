"""확인 실행기 + 주기 실행 스케줄러."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from . import config as config_mod
from . import digest as digest_mod
from . import extract as extract_mod
from . import fetch as fetch_mod
from . import notify as notify_mod
from . import store as store_mod

LOG_MAX_BYTES = 2_000_000


# 예약 실행이 조금 늦거나 이르게 와도 주기가 밀리지 않도록 두는 여유.
DUE_GRACE = timedelta(minutes=5)

ISSUE_LABELS = {
    "no_items": "목록을 하나도 읽지 못했습니다",
    "drop": "항목 수가 갑자기 크게 줄었습니다",
    "method_changed": "공고를 읽어 오는 방식이 바뀌었습니다",
    "relisted": "기억하던 공고와 지금 목록이 하나도 겹치지 않습니다",
    "fingerprint": "공고 목록을 인식하지 못해 페이지 변경만 감시하고 있습니다",
    "errors": "여러 번 연속으로 접속에 실패했습니다",
    "stale": "오랫동안 새 공고가 하나도 없습니다",
}


def _median(values: list) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _parse_iso(value: str):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class Monitor:
    """설정/상태 파일을 다루고 확인을 실행하는 중심 객체."""

    def __init__(self, config_path: str, state_path: str = "", log_path: str = ""):
        self.config_path = os.path.abspath(config_path)
        data_dir = os.path.join(os.path.dirname(self.config_path), "data")
        self.state_path = os.path.abspath(state_path or os.path.join(data_dir, "state.json"))
        self.log_path = os.path.abspath(log_path or os.path.join(data_dir, "monitor.log"))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.last_scheduler_tick = ""

    # ------------------------------------------------------------ 파일 입출력
    def load_config(self) -> dict:
        with self._lock:
            return config_mod.load(self.config_path)

    def save_config(self, cfg: dict) -> dict:
        with self._lock:
            return config_mod.save(self.config_path, cfg)

    def load_state(self) -> dict:
        with self._lock:
            return store_mod.load(self.state_path)

    def save_state(self, state: dict) -> None:
        with self._lock:
            store_mod.save(self.state_path, state)

    def log(self, message: str) -> None:
        line = f"[{store_mod.now_iso()}] {message}"
        print(line, flush=True)
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > LOG_MAX_BYTES:
                os.replace(self.log_path, self.log_path + ".1")
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def read_log(self, lines: int = 200) -> list:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().splitlines()[-lines:]
        except OSError:
            return []

    # ------------------------------------------------------------ 확인 로직
    def fetch_site(self, cfg: dict, site: dict):
        request_cfg = cfg.get("request") or {}
        timeout = float(request_cfg.get("timeout_sec") or 20)
        user_agent = request_cfg.get("user_agent") or fetch_mod.DEFAULT_UA
        if (site.get("mode") or "auto") == "browser":
            return fetch_mod.fetch_rendered(site["url"], timeout=max(timeout, 30), user_agent=user_agent,
                                            selector=site.get("selector") or "")
        return fetch_mod.fetch(
            site["url"], timeout=timeout, user_agent=user_agent,
            headers=site.get("headers") or None,
            method=site.get("method") or "GET",
            body=site.get("body") or "",
        )

    def collect(self, cfg: dict, site: dict):
        """사이트의 공고 목록을 모은다. 여러 쪽으로 나뉘어 있으면 이어서 읽는다.

        주소나 본문에 {page} 가 있고 pages 가 2 이상이면 1쪽부터 차례로 요청한다.
        중간 쪽이 실패하거나 빈 목록이면 거기서 멈추고 그때까지 모은 것을 쓴다.
        """
        pages = int(site.get("pages") or 1)
        url_template, body_template = site["url"], site.get("body") or ""
        paged = pages > 1 and ("{page}" in url_template or "{page}" in body_template)
        if not paged:
            fetched = self.fetch_site(cfg, site)
            return fetched, extract_mod.extract(site, fetched)

        first_fetched, merged, seen_ids = None, [], set()
        note_parts = []
        for page in range(1, pages + 1):
            page_site = dict(site,
                             url=url_template.replace("{page}", str(page)),
                             body=body_template.replace("{page}", str(page)))
            try:
                fetched = self.fetch_site(cfg, page_site)
            except fetch_mod.FetchError as exc:
                if first_fetched is None:
                    raise
                note_parts.append(f"{page}쪽부터 읽지 못했습니다({exc}).")
                break
            extracted = extract_mod.extract(page_site, fetched)
            if first_fetched is None:
                first_fetched, method, fingerprint = fetched, extracted.method, extracted.fingerprint
            new_on_page = [i for i in extracted.items if i["id"] not in seen_ids]
            for item in new_on_page:
                seen_ids.add(item["id"])
            merged.extend(new_on_page)
            if not new_on_page:
                break                      # 더 볼 쪽이 없다
        note = " ".join(note_parts)
        if len(merged) > int(site.get("max_items") or 300):
            merged = merged[: int(site.get("max_items") or 300)]
        return first_fetched, extract_mod.ExtractResult(
            items=merged, method=method, fingerprint=fingerprint, note=note)

    @staticmethod
    def _relisted(entry: dict, items: list) -> bool:
        """기억하던 공고와 이번에 읽은 공고가 하나도 겹치지 않는지.

        설정을 고쳐 목록을 더 정확히 읽게 되면 공고 식별자가 통째로 달라진다.
        이때 전부 새 공고로 취급하면 메일이 쏟아지므로, 그런 경우를 가려낸다.
        (원래 기억하던 것이 몇 건뿐이면 우연일 수 있어 5건 이상일 때만 본다)
        """
        seen = entry.get("seen") or {}
        if len(seen) < 5 or not items:
            return False
        return not any(item.get("id") in seen for item in items)

    def check_site(self, cfg: dict, site: dict, state: dict, force: bool = False) -> dict:
        """사이트 한 곳을 확인하고 결과 딕셔너리를 돌려준다. 상태도 갱신한다."""
        entry = store_mod.site_state(state, site["id"])
        at = store_mod.now_iso()
        before = {                        # 이번 확인을 기록하기 전의 상태 (이상 감지 기준)
            "method": entry.get("last_method") or "",
            "counts": store_mod.recent_item_counts(entry),
        }
        result = {
            "site_id": site["id"],
            "site_name": site.get("name") or site["url"],
            "site_url": site["url"],
            "site_link": config_mod.site_link(site),   # 사람이 열어 볼 주소
            "at": at,
            "status": "ok",
            "new_items": [],
            "item_count": 0,
            "method": "",
            "note": "",
            "error": "",
            "alert": None,
            "relisted": False,
        }
        try:
            fetched, extracted = self.collect(cfg, site)
        except fetch_mod.FetchError as exc:
            result.update(status="error", error=str(exc))
            store_mod.record_check(entry, at=at, status="error", error=str(exc))
            result["alert"] = self.update_health(cfg, site, entry, result, at, before)
            self.log(f"[{site['id']}] 확인 실패: {exc}")
            return result

        link = result.get("site_link") or site["url"]
        for item in extracted.items:
            # 항목에 쓸 만한 상세 주소가 없으면(요청 주소이거나 사이트 최상위) 사람이 볼 주소로 연결한다
            url = item.get("url") or ""
            path = urlparse(url).path if url else ""
            if not url or url == fetched.url or path in ("", "/"):
                item["url"] = link
        result["method"] = extracted.method
        result["note"] = extracted.note
        result["item_count"] = len(extracted.items)
        first_run = not entry.get("baseline_done")

        if extracted.method == "fingerprint":
            changed = bool(entry.get("fingerprint")) and entry["fingerprint"] != extracted.fingerprint
            if changed and not force:
                result["new_items"] = [{
                    "title": "페이지 내용이 변경되었습니다 (목록 자동 인식 불가)",
                    "url": site["url"],
                    "date": "",
                    "id": extracted.fingerprint,
                }]
                result["status"] = "changed"
            elif first_run:
                result["status"] = "baseline"
            entry["baseline_done"] = True
            store_mod.record_check(
                entry, at=at, status=result["status"], new_items=result["new_items"],
                method=extracted.method, item_count=0, fingerprint=extracted.fingerprint,
                note=extracted.note, sample_titles=[],
            )
        else:
            new_items = store_mod.diff_new(entry, extracted.items)
            if first_run and not cfg.get("notify_on_first_run"):
                result["status"] = "baseline"
                result["note"] = (result["note"] + " " if result["note"] else "") + \
                    f"첫 확인이라 현재 {len(extracted.items)}건을 기준으로 저장했습니다(알림 없음)."
                new_items = []
            elif self._relisted(entry, extracted.items):
                # 기억하던 공고와 이번 목록이 하나도 겹치지 않는다. 사이트가 하루아침에
                # 전부 바뀌었을 리는 없으니, 읽는 방법이 달라진 것으로 보고 기준을 다시 잡는다.
                # (그대로 알렸다가는 이미 있던 공고 수십·수백 건이 한꺼번에 메일로 나간다)
                result["status"] = "baseline"
                result["relisted"] = True
                result["note"] = (result["note"] + " " if result["note"] else "") + \
                    (f"이전에 기억하던 {len(entry.get('seen') or {})}건과 겹치는 공고가 없어, "
                     f"지금 {len(extracted.items)}건을 기준으로 다시 저장했습니다(알림 없음).")
                new_items = []
            elif new_items:
                result["status"] = "new"
            result["new_items"] = new_items
            store_mod.remember(entry, extracted.items, at)
            entry["baseline_done"] = True
            store_mod.record_check(
                entry, at=at, status=result["status"], new_items=new_items,
                method=extracted.method, item_count=len(extracted.items),
                fingerprint=extracted.fingerprint, note=result["note"],
                sample_titles=[i.get("title", "") for i in extracted.items[:5]],
                # 기준을 다시 잡았다면 여기부터 새로 센다 (예전 숫자와 비교하지 않도록)
                reset=result["relisted"],
            )

        result["alert"] = self.update_health(cfg, site, entry, result, at, before)
        if result["alert"]:
            mark = "정상 복구" if result["alert"]["recovered"] else "이상 감지"
            self.log(f"[{site['id']}] {mark}: {result['alert']['label']} · {result['alert']['detail']}")

        self.log(
            f"[{site['id']}] {result['status']} · 항목 {result['item_count']}개 · "
            f"새 공고 {len(result['new_items'])}건 · 방식 {extracted.method}"
            + (f" · {result['note']}" if result["note"] else "")
        )
        return result

    # ------------------------------------------------------------ 이상 감지
    def diagnose_health(self, cfg: dict, site: dict, entry: dict, result: dict, at: str, before: dict):
        """사이트가 조용히 망가진 정황을 찾는다. (issue, 설명) 또는 (None, "").

        before 에는 이번 확인을 기록하기 *전* 의 값(평소 항목 수, 직전 추출 방식)이 들어온다.
        """
        settings = cfg.get("alerts") or {}
        if not settings.get("enabled"):
            return None, ""

        if result["status"] == "error":
            limit = int(settings.get("consecutive_errors") or 3)
            count = int(entry.get("consecutive_errors") or 0)
            if count >= limit:
                return "errors", f"{count}번 연속 실패 · 마지막 오류: {result['error']}"
            return None, ""

        if result.get("relisted"):
            return "relisted", ("읽는 방식이 바뀐 것으로 보고 지금 목록을 기준으로 다시 저장했습니다. "
                                "설정을 바꾼 직후라면 정상입니다. 그런 적이 없다면 사이트 구조가 "
                                "변경됐을 수 있으니 설정 화면에서 수집 예시를 확인해 주세요.")
        baseline = _median(before.get("counts") or [])
        method = result["method"]
        previous_method = before.get("method") or ""

        if method == "fingerprint" and result["item_count"] == 0:
            return "fingerprint", "목록을 못 찾아 본문 변경만 감시 중입니다. 확인 방식이나 주소를 다시 지정해 주세요."
        if result["item_count"] == 0 and baseline >= 1:
            return "no_items", f"평소 {baseline:.0f}건 정도 읽었는데 이번에는 0건입니다."
        ratio = float(settings.get("drop_ratio") or 0.5)
        if baseline >= 5 and result["item_count"] < baseline * ratio:
            return "drop", f"평소 {baseline:.0f}건에서 {result['item_count']}건으로 줄었습니다."
        if previous_method and method and previous_method != method:
            return "method_changed", f"{previous_method} → {method} 로 바뀌었습니다. 사이트 구조가 변경됐을 수 있습니다."

        stale_days = int(settings.get("stale_days") or 0)
        if stale_days and entry.get("baseline_done"):
            since = _parse_iso(entry.get("last_new_at") or "") or _parse_iso(entry.get("first_baseline_at") or "")
            if since is None:
                since = _parse_iso(entry.get("last_check") or "")
            now = _parse_iso(at)
            if since and now and (now - since) >= timedelta(days=stale_days):
                days = (now - since).days
                return "stale", f"{days}일 동안 새 공고가 없습니다. 공고가 아니라 메뉴 같은 것을 감시하고 있지 않은지 확인해 주세요."
        return None, ""

    def update_health(self, cfg: dict, site: dict, entry: dict, result: dict, at: str, before: dict):
        """이상이 새로 생겼을 때만 알림 대상으로 돌려준다 (같은 문제로 매번 보내지 않음)."""
        issue, detail = self.diagnose_health(cfg, site, entry, result, at, before)
        health = entry.setdefault("health", {"issue": "", "detail": "", "since": ""})
        previous_issue = health.get("issue") or ""
        alert = None
        if issue and issue != previous_issue:
            health.update({"issue": issue, "detail": detail, "since": at})
            alert = {
                "site_id": site["id"], "site_name": result["site_name"], "site_url": result["site_url"],
                "site_link": result.get("site_link") or result["site_url"],
                "kind": issue, "label": ISSUE_LABELS.get(issue, issue), "detail": detail, "recovered": False,
            }
        elif issue:
            health["detail"] = detail
        elif previous_issue:
            health.update({"issue": "", "detail": "", "since": ""})
            alert = {
                "site_id": site["id"], "site_name": result["site_name"], "site_url": result["site_url"],
                "site_link": result.get("site_link") or result["site_url"],
                "kind": previous_issue, "label": ISSUE_LABELS.get(previous_issue, previous_issue),
                "detail": "다시 정상으로 확인됐습니다.", "recovered": True,
            }
        return alert

    def next_due(self, cfg: dict, state: dict, site: dict):
        """다음 확인 예정 시각. 한 번도 확인한 적 없으면 None (= 지금 바로 확인 대상)."""
        entry = (state.get("sites") or {}).get(site["id"]) or {}
        last = _parse_iso(entry.get("last_check") or "")
        if not last:
            return None
        return last + timedelta(hours=config_mod.interval_hours(cfg, site))

    def is_due(self, cfg: dict, state: dict, site: dict, now=None) -> bool:
        """확인할 때가 됐는지. 조금 이른 것은 봐 준다(DUE_GRACE).

        GitHub 예약 실행은 정확한 시각에 오지 않는다. 딱 맞춰 자르면
        '1시간 주기'가 매번 몇 분씩 밀려 결국 1시간 20분, 40분… 으로 늘어난다.
        """
        due = self.next_due(cfg, state, site)
        return due is None or (due - DUE_GRACE) <= (now or datetime.now().astimezone())

    def due_sites(self, cfg: dict, state: dict, now=None) -> list:
        now = now or datetime.now().astimezone()
        return [
            site for site in cfg.get("sites") or []
            if site.get("enabled") and self.is_due(cfg, state, site, now)
        ]

    def run_check(self, site_ids=None, force: bool = False, notify: bool = True,
                  only_due: bool = False) -> dict:
        """대상 사이트를 확인하고, 새 공고가 있으면 메일 한 통으로 알린다."""
        with self._lock:
            cfg = self.load_config()
            state = self.load_state()

            if site_ids:
                # 특정 사이트를 콕 집었을 때는 '사용 안 함' 이어도 확인한다.
                wanted = set(site_ids)
                targets = [s for s in (cfg.get("sites") or []) if s["id"] in wanted]
            elif only_due:
                targets = self.due_sites(cfg, state)
            else:
                targets = [s for s in (cfg.get("sites") or []) if s.get("enabled")]

            results = [self.check_site(cfg, site, state, force=force) for site in targets]
            alerts = [r["alert"] for r in results if r.get("alert")]
            at = store_mod.now_iso()
            # '이미 아는 공고' 기록과 '보낼 목록' 은 반드시 함께 저장한다. 따로 저장하면
            # 그 사이에 실행이 끊겼을 때 공고가 아는 것으로만 남고 메일에는 못 실린다.
            store_mod.seed_archive(state, {s["id"]: s.get("name") or s["id"]
                                           for s in (cfg.get("sites") or [])})
            store_mod.hold(state, results, alerts, at)
            self.save_state(state)

        summary = {
            "at": at,
            "checked": len(results),
            "new_total": sum(len(r["new_items"]) for r in results),
            "results": results,
            "alerts": alerts,
            "email": {"sent": False, "error": "", "skipped": ""},
        }

        box = store_mod.outbox(state)
        pending_items, pending_alerts = box["items"], box["alerts"]
        summary["pending"] = {
            "items": len(pending_items),
            "alerts": len(pending_alerts),
            "since": box.get("since", ""),
            "schedule": digest_mod.describe(cfg) if (cfg.get("digest") or {}).get("enabled") else "",
        }
        if not notify:
            if pending_items or pending_alerts:
                summary["email"]["skipped"] = "메일 발송을 건너뛰도록 지정했습니다."
            return summary

        now = datetime.now().astimezone()
        last_sent = _parse_iso(box.get("last_sent") or "")
        if not digest_mod.should_send(cfg, last_sent, now):
            nxt = digest_mod.next_slot(cfg, now)
            when = nxt.strftime("%m-%d %H:%M") if nxt else "다음 발송 시각"
            if pending_items or pending_alerts:
                summary["email"]["skipped"] = (
                    f"모아 두는 중입니다 (새 공고 {len(pending_items)}건 · 점검 {len(pending_alerts)}건). "
                    f"{when} 에 보냅니다.")
                self.log(summary["email"]["skipped"])
            return summary
        if not (pending_items or pending_alerts):
            return summary

        live = config_mod.effective(cfg)   # 시크릿(환경변수)을 얹은 설정
        email_cfg = live.get("email") or {}
        prefix = email_cfg.get("subject_prefix") or "[채용 알림]"
        # 새 공고는 설정 화면 수신처 + 관리자에게, 점검 안내는 관리자에게만 간다.
        posting_to = live.get("recipients") or []
        admin_to = live.get("admin_recipients") or posting_to

        mails = []
        if pending_items:
            held = store_mod.held_results(state)
            subject, text, html = notify_mod.render(held, prefix, since=box.get("since", ""),
                                                    page_url=cfg.get("page_url") or "")
            mails.append(("새 공고", posting_to, subject, text, html))
        if pending_alerts:
            subject, text, html = notify_mod.render_alerts(pending_alerts, results, prefix,
                                                           page_url=cfg.get("page_url") or "")
            mails.append(("점검 안내", admin_to, subject, text, html))

        if not email_cfg.get("enabled"):
            summary["email"]["skipped"] = "메일 발송이 꺼져 있습니다."
        else:
            skipped, errors, done = [], [], set()
            for kind, recipients, subject, text, html in mails:
                if not recipients:
                    skipped.append(f"{kind}: 수신 이메일이 없습니다.")
                    continue
                try:
                    notify_mod.send(email_cfg, recipients, subject, text, html)
                    summary["email"]["sent"] = True
                    done.add(kind)
                    self.log(f"{kind} 메일 발송 완료 → {', '.join(recipients)}")
                except notify_mod.NotifyError as exc:
                    errors.append(f"{kind}: {exc}")
                    self.log(f"{kind} 메일 발송 실패: {exc}")
            summary["email"]["error"] = " / ".join(errors)
            summary["email"]["skipped"] = " / ".join(skipped)
            if done:
                # 나간 것만 비운다. 실패한 쪽은 남겨 두었다가 다음 확인 때 다시 보낸다.
                with self._lock:
                    store_mod.clear_outbox(state, store_mod.now_iso(),
                                           items="새 공고" in done, alerts="점검 안내" in done)
                    self.save_state(state)
                box = store_mod.outbox(state)
                summary["pending"] = {"items": len(box["items"]), "alerts": len(box["alerts"]),
                                      "since": box.get("since", ""),
                                      "schedule": summary["pending"]["schedule"]}
        if summary["email"]["skipped"]:
            self.log(f"메일 발송 생략: {summary['email']['skipped']} (새 공고 {summary['new_total']}건)")
        return summary

    def backfill(self) -> dict:
        """지금 각 사이트에 올라와 있는 공고를 이력에 채워 넣는다 (메일은 보내지 않는다).

        이미 '아는 공고' 로 기억만 하고 있던 것들을 설정 화면의 공고 이력에서도
        볼 수 있게 하는 용도다. 처음 이력을 만들 때 한 번 쓰면 된다.
        기억해 둔 첫 발견 시각이 있으면 그 시각을 그대로 쓴다.
        """
        cfg = self.load_config()
        state = self.load_state()
        now = store_mod.now_iso()
        summary = {"at": now, "checked": 0, "added": 0, "sites": []}
        for site in cfg.get("sites") or []:
            if not site.get("enabled"):
                continue
            try:
                fetched, extracted = self.collect(cfg, site)
            except fetch_mod.FetchError as exc:
                summary["sites"].append({"site_id": site["id"], "error": str(exc), "added": 0})
                self.log(f"[{site['id']}] 이력 채우기 실패: {exc}")
                continue
            entry = store_mod.site_state(state, site["id"])
            seen = entry.get("seen") or {}
            link = config_mod.site_link(site)
            rows = []
            for item in extracted.items:
                url = item.get("url") or ""
                path = urlparse(url).path if url else ""
                if not url or url == fetched.url or path in ("", "/"):
                    url = link
                remembered = seen.get(item.get("id") or "") or {}
                rows.append({
                    "site_id": site["id"],
                    "site_name": site.get("name") or site["id"],
                    "site_link": link,
                    "title": item.get("title", ""),
                    "url": url,
                    "date": item.get("date", ""),
                    "found_at": remembered.get("first_seen") or now,
                })
            with self._lock:
                added = store_mod.add_to_archive(state, rows)
            summary["checked"] += 1
            summary["added"] += added
            summary["sites"].append({"site_id": site["id"], "found": len(rows), "added": added})
            self.log(f"[{site['id']}] 이력 채우기: 읽은 공고 {len(rows)}건 · 새로 넣은 것 {added}건")
        with self._lock:
            self.save_state(state)
        self.log(f"이력 채우기 완료: 사이트 {summary['checked']}곳 · 모두 {summary['added']}건 추가")
        return summary

    def preview(self, site: dict) -> dict:
        """설정 화면에서 '미리보기' 를 눌렀을 때: 지금 무엇이 뽑히는지만 보여 준다."""
        cfg = self.load_config()
        site = config_mod.normalize({"sites": [site]})["sites"]
        if not site:
            return {"ok": False, "error": "주소가 비어 있습니다."}
        site = site[0]
        try:
            fetched, extracted = self.collect(cfg, site)
        except fetch_mod.FetchError as exc:
            return {"ok": False, "error": str(exc)}
        extracted = extract_mod.extract(site, fetched)
        return {
            "ok": True,
            "status": fetched.status,
            "rendered": fetched.rendered,
            "method": extracted.method,
            "note": extracted.note,
            "count": len(extracted.items),
            "items": extracted.items[:20],
        }

    # ------------------------------------------------------------ 스케줄러
    def scheduler_loop(self, tick_seconds: int = 30) -> None:
        self.log("주기 확인 스케줄러 시작")
        while not self._stop.is_set():
            try:
                cfg = self.load_config()
                state = self.load_state()
                due = self.due_sites(cfg, state)
                self.last_scheduler_tick = store_mod.now_iso()
                if due:
                    self.log(f"예정된 확인 {len(due)}건 실행: {', '.join(s['id'] for s in due)}")
                    self.run_check(site_ids=[s["id"] for s in due], notify=True)
            except Exception as exc:  # 스케줄러는 어떤 오류에도 멈추지 않는다
                self.log(f"스케줄러 오류: {exc}")
            self._stop.wait(tick_seconds)
        self.log("주기 확인 스케줄러 종료")

    def start_scheduler(self, tick_seconds: int = 30) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.scheduler_loop, args=(tick_seconds,), daemon=True)
        self._thread.start()

    def stop_scheduler(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_forever(self, tick_seconds: int = 30) -> None:
        """웹 UI 없이 주기 확인만 돌릴 때 사용."""
        self.start_scheduler(tick_seconds)
        try:
            while self._thread and self._thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop_scheduler()
