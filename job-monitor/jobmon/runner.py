"""확인 실행기 + 주기 실행 스케줄러."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta

from . import config as config_mod
from . import extract as extract_mod
from . import fetch as fetch_mod
from . import notify as notify_mod
from . import store as store_mod

LOG_MAX_BYTES = 2_000_000


ISSUE_LABELS = {
    "no_items": "목록을 하나도 읽지 못했습니다",
    "drop": "항목 수가 갑자기 크게 줄었습니다",
    "method_changed": "공고를 읽어 오는 방식이 바뀌었습니다",
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
            return fetch_mod.fetch_rendered(site["url"], timeout=max(timeout, 30), user_agent=user_agent)
        return fetch_mod.fetch(
            site["url"], timeout=timeout, user_agent=user_agent,
            headers=site.get("headers") or None,
            method=site.get("method") or "GET",
            body=site.get("body") or "",
        )

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
        }
        try:
            fetched = self.fetch_site(cfg, site)
        except fetch_mod.FetchError as exc:
            result.update(status="error", error=str(exc))
            store_mod.record_check(entry, at=at, status="error", error=str(exc))
            result["alert"] = self.update_health(cfg, site, entry, result, at, before)
            self.log(f"[{site['id']}] 확인 실패: {exc}")
            return result

        extracted = extract_mod.extract(site, fetched)
        link = result.get("site_link") or site["url"]
        for item in extracted.items:
            # 항목에 상세 주소가 없으면 API 주소 대신 사람이 볼 주소로 연결한다
            if not item.get("url") or item["url"] == fetched.url:
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
        due = self.next_due(cfg, state, site)
        return due is None or due <= (now or datetime.now().astimezone())

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
            self.save_state(state)

        alerts = [r["alert"] for r in results if r.get("alert")]
        summary = {
            "at": store_mod.now_iso(),
            "checked": len(results),
            "new_total": sum(len(r["new_items"]) for r in results),
            "results": results,
            "alerts": alerts,
            "email": {"sent": False, "error": "", "skipped": ""},
        }

        worth_mailing = summary["new_total"] or alerts
        if not notify or not worth_mailing:
            if worth_mailing and not notify:
                summary["email"]["skipped"] = "메일 발송을 건너뛰도록 지정했습니다."
            return summary

        live = config_mod.effective(cfg)   # 시크릿(환경변수)을 얹은 설정
        email_cfg = live.get("email") or {}
        recipients = live.get("recipients") or []
        if not email_cfg.get("enabled"):
            summary["email"]["skipped"] = "메일 발송이 꺼져 있습니다."
        elif not recipients:
            summary["email"]["skipped"] = "수신 이메일이 없습니다."
        else:
            subject, text, html = notify_mod.render(
                results, email_cfg.get("subject_prefix") or "[채용 알림]", alerts=alerts)
            try:
                notify_mod.send(email_cfg, recipients, subject, text, html)
                summary["email"]["sent"] = True
                self.log(f"메일 발송 완료 → {', '.join(recipients)} "
                         f"(새 공고 {summary['new_total']}건 · 점검 알림 {len(alerts)}건)")
            except notify_mod.NotifyError as exc:
                summary["email"]["error"] = str(exc)
                self.log(f"메일 발송 실패: {exc}")
        if summary["email"]["skipped"]:
            self.log(f"메일 발송 생략: {summary['email']['skipped']} (새 공고 {summary['new_total']}건)")
        return summary

    def preview(self, site: dict) -> dict:
        """설정 화면에서 '미리보기' 를 눌렀을 때: 지금 무엇이 뽑히는지만 보여 준다."""
        cfg = self.load_config()
        site = config_mod.normalize({"sites": [site]})["sites"]
        if not site:
            return {"ok": False, "error": "주소가 비어 있습니다."}
        site = site[0]
        try:
            fetched = self.fetch_site(cfg, site)
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
