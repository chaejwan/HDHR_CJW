"""단위/통합 테스트. 외부 인터넷 없이 로컬 임시 서버로만 확인한다.

  python3 -m unittest discover -s tests -v      (job-monitor 폴더에서)
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from datetime import datetime, timedelta, timezone   # noqa: E402
from jobmon import config as config_mod       # noqa: E402
from jobmon import digest as digest_mod       # noqa: E402
from jobmon import extract as extract_mod     # noqa: E402
from jobmon import notify as notify_mod       # noqa: E402
from jobmon import server as server_mod       # noqa: E402
from jobmon import store as store_mod         # noqa: E402
from jobmon import fetch as fetch_mod         # noqa: E402
from jobmon.fetch import FetchResult          # noqa: E402
from jobmon.runner import Monitor             # noqa: E402


def fake(text, url="https://example.com/careers", content_type="text/html"):
    return FetchResult(url=url, status=200, text=text, content_type=content_type)


class ExtractTest(unittest.TestCase):
    def test_links(self):
        html = """
        <html><body>
          <a href="/jobs/1">백엔드 개발자 채용</a>
          <a href="/jobs/2">데이터 분석가 채용</a>
          <a href="javascript:void(0)">더보기</a>
          <a href="/jobs/1">백엔드 개발자 채용</a>
        </body></html>"""
        result = extract_mod.extract({"mode": "auto"}, fake(html))
        self.assertEqual(result.method, "links")
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0]["url"], "https://example.com/jobs/1")

    def test_filters(self):
        html = '<a href="/jobs/1">개발자 모집</a><a href="/help">이용약관</a>'
        site = {"mode": "auto", "url_pattern": "jobs", "exclude_pattern": "약관"}
        result = extract_mod.extract(site, fake(html))
        self.assertEqual([i["title"] for i in result.items], ["개발자 모집"])

    def test_jsonld(self):
        payload = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "JobPosting", "title": "프론트엔드 개발자",
                 "url": "https://example.com/jobs/9", "datePosted": "2026-09-01"},
                {"@type": "WebPage", "name": "그냥 페이지"},
            ],
        }
        html = f'<script type="application/ld+json">{json.dumps(payload)}</script><a href="/x">메뉴</a>'
        result = extract_mod.extract({"mode": "auto"}, fake(html))
        self.assertEqual(result.method, "jsonld")
        self.assertEqual(result.items[0]["title"], "프론트엔드 개발자")
        self.assertEqual(result.items[0]["date"], "2026-09-01")

    def test_embedded_next_data(self):
        payload = {"props": {"pageProps": {"list": [
            {"id": 11, "title": "채용 공고 A"},
            {"id": 12, "title": "채용 공고 B"},
        ]}}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        result = extract_mod.extract({"mode": "auto"}, fake(html))
        self.assertEqual(result.method, "embedded-json")
        self.assertEqual(len(result.items), 2)
        # 목록 항목에 개별 주소가 없으면 사이트 주소로 대체된다
        self.assertEqual(result.items[0]["url"], "https://example.com/careers")

    def test_json_api_with_mapping(self):
        body = json.dumps({"data": {"rows": [
            {"seq": 5, "nm": "경력 채용", "regDt": "2026-09-02"},
            {"seq": 6, "nm": "신입 채용", "regDt": "2026-09-03"},
        ]}})
        site = {
            "mode": "json",
            "json": {
                "items_path": "data.rows", "id_field": "seq", "title_field": "nm",
                "date_field": "regDt", "url_template": "https://example.com/jobs/{seq}",
            },
        }
        result = extract_mod.extract(site, fake(body, content_type="application/json"))
        self.assertEqual(result.method, "json")
        self.assertEqual(result.items[1]["url"], "https://example.com/jobs/6")
        self.assertEqual(result.items[1]["date"], "2026-09-03")

    def test_fingerprint_fallback(self):
        result = extract_mod.extract({"mode": "auto"}, fake("<html><body><p>공고 없음</p></body></html>"))
        self.assertEqual(result.method, "fingerprint")
        self.assertTrue(result.fingerprint)

    def test_max_items_guard(self):
        html = "".join(f'<a href="/j/{i}">공고 {i}</a>' for i in range(50))
        result = extract_mod.extract({"mode": "auto", "max_items": 10}, fake(html))
        self.assertEqual(len(result.items), 10)
        self.assertIn("10개만", result.note)

    def test_ids_are_stable(self):
        html = '<a href="/jobs/1?utm_source=x">개발자</a>'
        first = extract_mod.extract({"mode": "auto"}, fake(html))
        second = extract_mod.extract({"mode": "auto"}, fake('<a href="/jobs/1">개발자</a>'))
        self.assertEqual(first.items[0]["id"], second.items[0]["id"])


class ConfigTest(unittest.TestCase):
    def test_normalize_fills_defaults(self):
        cfg = config_mod.normalize({"sites": [{"url": "example.com/jobs", "name": "예시"}]})
        site = cfg["sites"][0]
        self.assertEqual(site["url"], "https://example.com/jobs")
        self.assertEqual(site["mode"], "auto")
        self.assertTrue(site["id"])
        self.assertEqual(cfg["check_interval_hours"], 24)

    def test_unique_ids_and_bad_values(self):
        cfg = config_mod.normalize({
            "check_interval_hours": -5,
            "recipients": "a@b.com, c@d.com",
            "sites": [
                {"url": "https://a.com", "name": "같은 이름"},
                {"url": "https://b.com", "name": "같은 이름"},
                {"url": ""},
            ],
        })
        ids = [s["id"] for s in cfg["sites"]]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(cfg["check_interval_hours"], 24)
        self.assertEqual(cfg["recipients"], ["a@b.com", "c@d.com"])

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            saved = config_mod.save(path, {"sites": [{"url": "https://a.com", "name": "A"}],
                                           "check_interval_hours": 6})
            loaded = config_mod.load(path)
            self.assertEqual(loaded["check_interval_hours"], 6)
            self.assertEqual(loaded["sites"][0]["id"], saved["sites"][0]["id"])
            self.assertEqual(oct(os.stat(path).st_mode)[-3:], "600")

    def test_password_is_masked_and_kept(self):
        cfg = config_mod.normalize({"email": {"password": "secret"}})
        public = server_mod._public_config(cfg)
        self.assertEqual(public["email"]["password"], "")
        self.assertTrue(public["email"]["password_saved"])
        merged = server_mod._merge_password(public, cfg)
        self.assertEqual(merged["email"]["password"], "secret")
        cleared = server_mod._merge_password({"email": {"password": "__CLEAR__"}}, cfg)
        self.assertEqual(cleared["email"]["password"], "")

    def test_validate_warnings(self):
        cfg = config_mod.normalize({"email": {"enabled": True, "password_env": "NOPE_NOT_SET"},
                                    "sites": [{"url": "https://a.com", "url_pattern": "["}]})
        warnings = config_mod.validate(cfg)
        self.assertTrue(any("수신 이메일" in w for w in warnings))
        self.assertTrue(any("정규식 오류" in w for w in warnings))


class StoreTest(unittest.TestCase):
    def test_diff_and_remember(self):
        entry = store_mod.site_state(store_mod.empty_state(), "s1")
        items = [{"id": "a", "title": "A", "url": "u"}, {"id": "b", "title": "B", "url": "u"}]
        self.assertEqual(len(store_mod.diff_new(entry, items)), 2)
        store_mod.remember(entry, items, store_mod.now_iso())
        self.assertEqual(store_mod.diff_new(entry, items), [])
        newer = items + [{"id": "c", "title": "C", "url": "u"}]
        self.assertEqual([i["id"] for i in store_mod.diff_new(entry, newer)], ["c"])

    def test_prune_keeps_newest(self):
        entry = store_mod.site_state(store_mod.empty_state(), "s1")
        total = store_mod.MAX_SEEN_PER_SITE + 10
        store_mod.remember(entry, [{"id": f"old{i}", "title": "x", "url": "u"} for i in range(total)],
                           "2020-01-01T00:00:00+09:00")
        store_mod.remember(entry, [{"id": "fresh", "title": "x", "url": "u"}], "2030-01-01T00:00:00+09:00")
        self.assertLessEqual(len(entry["seen"]), store_mod.MAX_SEEN_PER_SITE)
        self.assertIn("fresh", entry["seen"])

    def test_history_capped(self):
        entry = store_mod.site_state(store_mod.empty_state(), "s1")
        for _ in range(store_mod.MAX_HISTORY_PER_SITE + 5):
            store_mod.record_check(entry, at=store_mod.now_iso(), status="ok")
        self.assertEqual(len(entry["history"]), store_mod.MAX_HISTORY_PER_SITE)


class NotifyRenderTest(unittest.TestCase):
    def test_subject_and_body(self):
        results = [{
            "site_name": "한화 채용", "site_url": "https://example.com", "status": "new",
            "new_items": [{"title": "개발자 모집", "url": "https://example.com/1", "date": "2026-09-05"}],
        }]
        subject, text, html = notify_mod.render(results)
        self.assertIn("새 공고 1건", subject)
        self.assertIn("개발자 모집", text)
        self.assertIn("https://example.com/1", html)

    def test_errors_go_to_the_admin_mail_only(self):
        """접속 실패 같은 운영 이야기는 공고 메일이 아니라 점검 안내 메일에 담긴다."""
        results = [
            {"site_name": "A", "site_url": "u", "status": "error", "error": "HTTP 500", "new_items": []},
            {"site_name": "B", "site_url": "u", "status": "new",
             "new_items": [{"title": "공고 제목", "url": "u", "date": ""}]},
        ]
        _subject, posting_text, _html = notify_mod.render(results)
        self.assertNotIn("HTTP 500", posting_text)
        self.assertIn("공고 제목", posting_text)

        alerts = [{"site_name": "A", "site_url": "u", "site_link": "u", "kind": "errors",
                   "label": "여러 번 연속으로 접속에 실패했습니다", "detail": "3번 연속 실패",
                   "recovered": False}]
        _subject, admin_text, _html = notify_mod.render_alerts(alerts, results)
        self.assertIn("HTTP 500", admin_text)
        self.assertIn("3번 연속 실패", admin_text)
        self.assertNotIn("공고 제목", admin_text)


# ---------------------------------------------------------------- 통합 테스트

PAGE = """<html><body><ul>{items}</ul></body></html>"""


class _SiteHandler(BaseHTTPRequestHandler):
    items = ['<li><a href="/jobs/1">첫 번째 공고</a></li>']

    def do_GET(self):
        body = PAGE.format(items="".join(self.items)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        _SiteHandler.items = ['<li><a href="/jobs/1">첫 번째 공고</a></li>']
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        config_mod.save(self.config_path, {
            "check_interval_hours": 24,
            "sites": [{"id": "local", "name": "테스트", "url": f"http://127.0.0.1:{self.port}/list"}],
        })
        self.monitor = Monitor(self.config_path)

    def test_baseline_then_new_item(self):
        first = self.monitor.run_check(notify=False)
        self.assertEqual(first["results"][0]["status"], "baseline")
        self.assertEqual(first["new_total"], 0)

        second = self.monitor.run_check(notify=False)
        self.assertEqual(second["results"][0]["status"], "ok")
        self.assertEqual(second["new_total"], 0)

        _SiteHandler.items.append('<li><a href="/jobs/2">두 번째 공고</a></li>')
        third = self.monitor.run_check(notify=False)
        self.assertEqual(third["new_total"], 1)
        self.assertEqual(third["results"][0]["new_items"][0]["title"], "두 번째 공고")

        fourth = self.monitor.run_check(notify=False)
        self.assertEqual(fourth["new_total"], 0)

    def test_notify_on_first_run_option(self):
        cfg = self.monitor.load_config()
        cfg["notify_on_first_run"] = True
        self.monitor.save_config(cfg)
        summary = self.monitor.run_check(notify=False)
        self.assertEqual(summary["new_total"], 1)

    def test_error_is_recorded(self):
        # 재시도 대기 때문에 테스트가 느려지지 않도록 잠깐 줄인다
        original = fetch_mod.RETRY_WAIT_SEC
        fetch_mod.RETRY_WAIT_SEC = (0, 0)
        self.addCleanup(lambda: setattr(fetch_mod, "RETRY_WAIT_SEC", original))
        cfg = self.monitor.load_config()
        cfg["sites"][0]["url"] = "http://127.0.0.1:9/none"
        self.monitor.save_config(cfg)
        summary = self.monitor.run_check(notify=False)
        self.assertEqual(summary["results"][0]["status"], "error")
        state = self.monitor.load_state()
        self.assertTrue(state["sites"]["local"]["last_error"])

    def test_due_scheduling(self):
        self.monitor.run_check(notify=False)
        cfg = self.monitor.load_config()
        state = self.monitor.load_state()
        self.assertEqual(self.monitor.due_sites(cfg, state), [])
        state["sites"]["local"]["last_check"] = "2000-01-01T00:00:00+09:00"
        self.monitor.save_state(state)
        state = self.monitor.load_state()
        self.assertEqual(len(self.monitor.due_sites(cfg, state)), 1)

    def test_preview(self):
        preview = self.monitor.preview({"name": "미리보기", "url": f"http://127.0.0.1:{self.port}/list"})
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["count"], 1)

    def test_reset_forgets_history(self):
        self.monitor.run_check(notify=False)
        state = self.monitor.load_state()
        store_mod.forget(state, "local")
        self.monitor.save_state(state)
        self.assertNotIn("local", self.monitor.load_state()["sites"])


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_path = os.path.join(self.tmp.name, "config.json")
        config_mod.save(config_path, {"sites": [{"id": "a", "name": "A", "url": "https://a.example"}],
                                      "email": {"password": "secret"}})
        self.monitor = Monitor(config_path)
        handler = type("Bound", (server_mod.Handler,), {"monitor": self.monitor})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, method="GET", payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urlrequest.Request(self.base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
        with urlrequest.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_index_page_is_served(self):
        with urlrequest.urlopen(self.base + "/", timeout=10) as resp:
            self.assertIn("채용공고 모니터", resp.read().decode("utf-8"))

    def test_config_get_hides_password(self):
        data = self.call("/api/config")
        self.assertTrue(data["ok"])
        self.assertEqual(data["config"]["email"]["password"], "")
        self.assertTrue(data["config"]["email"]["password_saved"])

    def test_config_put_keeps_password_and_adds_site(self):
        data = self.call("/api/config")
        cfg = data["config"]
        cfg["check_interval_hours"] = 3
        cfg["sites"].append({"name": "새 사이트", "url": "https://b.example"})
        saved = self.call("/api/config", "PUT", {"config": cfg})
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["config"]["check_interval_hours"], 3)
        self.assertEqual(len(saved["config"]["sites"]), 2)
        self.assertEqual(self.monitor.load_config()["email"]["password"], "secret")

    def test_state_endpoint(self):
        data = self.call("/api/state")
        self.assertEqual(data["state"]["sites"][0]["id"], "a")

    def test_unknown_api_is_404(self):
        with self.assertRaises(Exception):
            self.call("/api/nope")



class PatternExtractionTest(unittest.TestCase):
    """목록 조각만 돌려주는 사이트를 정규식으로 읽는다."""

    HTML = """
    <li><a href="/#none" data-value="23,055">
      <p class="company"> 삼성전자 DX부문</p>
      <h3 class="title">2026년 하반기 신입 채용 </h3>
      <p class="info"><span class="period"> 2026.09.08 ~ 2026.09.15 </span></p>
    </a></li>
    <li><a href="/#none" data-value="989">
      <p class="company"> 삼성전기</p>
      <h3 class="title">경력 채용 </h3>
      <p class="info"><span class="period"> 2026.09.01 ~ 2026.09.30 </span></p>
    </a></li>
    """
    PATTERN = (r'<a[^>]*data-value="(?P<no>[\d,]+)"[^>]*>'
               r'[\s\S]{0,400}?<p class="company">(?P<company>[^<]*)</p>'
               r'[\s\S]{0,400}?<h3 class="title">(?P<title>[^<]*)</h3>'
               r'[\s\S]{0,400}?<span class="period">(?P<period>[^<]*)</span>')
    MAPPING = {
        "title_template": "{company} {title}",
        "id_field": "no",
        "date_field": "period",
        "url_template": "https://example.com/hr/?no={no|digits}",
    }

    def test_items_and_links(self):
        items = extract_mod.items_from_pattern(
            self.PATTERN, self.HTML, "https://example.com/hr/", self.MAPPING)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "삼성전자 DX부문 2026년 하반기 신입 채용")
        self.assertEqual(items[0]["url"], "https://example.com/hr/?no=23055")
        self.assertEqual(items[0]["date"], "2026.09.08 ~ 2026.09.15")
        self.assertEqual(items[1]["url"], "https://example.com/hr/?no=989")

    def test_used_by_extract(self):
        site = config_mod.normalize({"sites": [{
            "name": "조각", "url": "https://example.com/hr/list.data", "mode": "html",
            "item_pattern": self.PATTERN, "json": self.MAPPING,
        }]})["sites"][0]
        fetched = FetchResult(url=site["url"], status=200, text=self.HTML,
                              content_type="text/html")
        result = extract_mod.extract(site, fetched)
        self.assertEqual(result.method, "pattern")
        self.assertEqual(len(result.items), 2)



class RelistedTest(unittest.TestCase):
    """설정을 고쳐 목록을 다르게 읽게 되면, 전부 새 공고로 알리지 않고 기준을 다시 잡는다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")
        config_mod.save(self.path, {"sites": [
            {"id": "s1", "name": "테스트", "url": "https://example.com/jobs"}]})
        self.monitor = Monitor(self.path)

    def _check(self, items, state):
        cfg = self.monitor.load_config()
        site = cfg["sites"][0]
        result = extract_mod.ExtractResult(items=items, method="links", fingerprint="fp")
        self.monitor.collect = lambda _cfg, _site: (
            FetchResult(url=site["url"], status=200, text="<html></html>",
                        content_type="text/html"), result)
        return self.monitor.check_site(cfg, site, state)

    def test_full_replacement_rebaselines_instead_of_mailing(self):
        state = {"sites": {}}
        old = [{"id": f"old{i}", "title": f"공고 {i}", "url": f"https://example.com/{i}", "date": ""}
               for i in range(8)]
        self.assertEqual(self._check(old, state)["status"], "baseline")   # 첫 확인

        # 설정을 고쳐 식별자가 통째로 달라진 상황
        renewed = [{"id": f"new{i}", "title": f"공고 {i}", "url": f"https://example.com/x/{i}", "date": ""}
                   for i in range(8)]
        result = self._check(renewed, state)
        self.assertEqual(result["status"], "baseline")
        self.assertEqual(result["new_items"], [])
        self.assertTrue(result["relisted"])
        self.assertEqual(result["alert"]["kind"], "relisted")

        # 그 다음 확인부터는 진짜 새 공고만 알린다
        plus_one = renewed + [{"id": "new99", "title": "진짜 새 공고",
                               "url": "https://example.com/x/99", "date": ""}]
        after = self._check(plus_one, state)
        self.assertEqual(after["status"], "new")
        self.assertEqual([i["id"] for i in after["new_items"]], ["new99"])

    def test_rebaseline_forgets_old_item_counts(self):
        """예전 방식으로 세던 항목 수 때문에 '급감' 오탐이 나지 않는다."""
        state = {"sites": {}}
        many = [{"id": f"old{i}", "title": f"메뉴 {i}", "url": f"https://example.com/{i}", "date": ""}
                for i in range(30)]
        for _ in range(3):
            self._check(many, state)                       # 평소 30건으로 기록됨

        few = [{"id": f"new{i}", "title": f"공고 {i}", "url": f"https://example.com/o/{i}", "date": ""}
               for i in range(14)]
        first = self._check(few, state)                    # 설정을 고쳐 진짜 공고 14건
        self.assertTrue(first["relisted"])

        after = self._check(few, state)                    # 그 다음 확인
        self.assertNotEqual((after["alert"] or {}).get("kind"), "drop")
        self.assertEqual(after["status"], "ok")

    def test_normal_new_posting_is_not_treated_as_relisted(self):
        state = {"sites": {}}
        items = [{"id": f"a{i}", "title": f"공고 {i}", "url": f"https://example.com/{i}", "date": ""}
                 for i in range(8)]
        self._check(items, state)
        added = items + [{"id": "a99", "title": "새 공고", "url": "https://example.com/99", "date": ""}]
        result = self._check(added, state)
        self.assertEqual(result["status"], "new")
        self.assertFalse(result["relisted"])


class DueGraceTest(unittest.TestCase):
    """예약 실행이 몇 분 늦게 와도 주기가 뒤로 밀리지 않는다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        path = os.path.join(self.dir, "config.json")
        config_mod.save(path, {"check_interval_hours": 1, "sites": [
            {"id": "s1", "name": "테스트", "url": "https://example.com/jobs"}]})
        self.monitor = Monitor(path)

    def _state(self, minutes_ago):
        from datetime import datetime, timedelta
        last = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
        return {"sites": {"s1": {"last_check": last.isoformat(), "baseline_done": True}}}

    def test_a_few_minutes_early_still_counts(self):
        cfg = self.monitor.load_config()
        site = cfg["sites"][0]
        self.assertTrue(self.monitor.is_due(cfg, self._state(57), site))   # 1시간 3분 전
        self.assertFalse(self.monitor.is_due(cfg, self._state(30), site))  # 아직 멀었다


class MailRoutingTest(unittest.TestCase):
    """새 공고는 설정 화면 수신처 + 관리자에게, 점검 안내는 관리자에게만."""

    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        config_mod.save(path, {
            "recipients": ["team@example.com"],
            "email": {"enabled": True, "smtp_host": "smtp.example", "username": "u@example.com"},
            "sites": [{"id": "s1", "name": "테스트", "url": "https://example.com/jobs"}],
        })
        self.monitor = Monitor(path)

        self.saved_env = os.environ.get("JOBMON_RECIPIENTS")
        os.environ["JOBMON_RECIPIENTS"] = "admin@example.com"
        self.addCleanup(self._restore_env)

        self.sent = []
        original_send = notify_mod.send
        notify_mod.send = lambda _cfg, to, subject, _t, _h="": self.sent.append((subject, list(to)))
        self.addCleanup(lambda: setattr(notify_mod, "send", original_send))

    def _restore_env(self):
        if self.saved_env is None:
            os.environ.pop("JOBMON_RECIPIENTS", None)
        else:
            os.environ["JOBMON_RECIPIENTS"] = self.saved_env

    def _serve(self, items):
        result = extract_mod.ExtractResult(items=items, method="links", fingerprint="fp")
        self.monitor.collect = lambda _cfg, site: (
            FetchResult(url=site["url"], status=200, text="<html></html>",
                        content_type="text/html"), result)

    @staticmethod
    def _items(prefix, count):
        return [{"id": f"{prefix}{i}", "title": f"공고 {prefix}{i}",
                 "url": f"https://example.com/{prefix}/{i}", "date": ""} for i in range(count)]

    def test_postings_and_alerts_go_to_different_people(self):
        self._serve(self._items("a", 8))
        self.monitor.run_check(notify=True)                 # 첫 확인: 기준만 저장
        self.assertEqual(self.sent, [])

        self._serve(self._items("b", 8))                    # 목록이 통째로 바뀜 → 점검 안내
        self.monitor.run_check(notify=True)
        self.assertEqual(len(self.sent), 1)
        subject, recipients = self.sent[0]
        self.assertIn("점검", subject)
        self.assertEqual(recipients, ["admin@example.com"])  # 팀에는 가지 않는다

        self.sent.clear()
        self._serve(self._items("b", 8) + [{"id": "b99", "title": "새 공고",
                                            "url": "https://example.com/b/99", "date": ""}])
        self.monitor.run_check(notify=True)
        # 새 공고 메일은 팀+관리자, 같은 확인에서 나온 '정상 복구' 안내는 관리자에게만
        posting = [m for m in self.sent if "새 공고" in m[0]]
        admin_only = [m for m in self.sent if "점검" in m[0]]
        self.assertEqual(len(posting), 1)
        self.assertEqual(posting[0][1], ["team@example.com", "admin@example.com"])
        self.assertEqual(len(admin_only), 1)
        self.assertEqual(admin_only[0][1], ["admin@example.com"])


class WorkflowSecretsTest(unittest.TestCase):
    """코드가 읽는 시크릿은 워크플로가 반드시 넘겨 주어야 한다.

    실제로 JOBMON_POSTING_RECIPIENTS 를 코드에만 넣고 워크플로에 빠뜨려,
    그 주소로 메일이 가지 않은 일이 있었다. 그 실수를 여기서 잡는다.
    """

    # 워크플로에 없어도 되는 것들 (평소에는 쓰지 않는 임시 조정용)
    OPTIONAL = {"JOBMON_EMAIL_ENABLED"}

    def test_every_secret_the_code_reads_is_passed_in(self):
        root = os.path.dirname(BASE_DIR)      # 저장소 최상위
        path = os.path.join(root, ".github", "workflows", "job-monitor.yml")
        if not os.path.exists(path):
            self.skipTest("워크플로 파일이 없는 곳에서 실행 중")
        with open(path, encoding="utf-8") as fh:
            workflow = fh.read()
        for name in config_mod.env_summary():
            if name in self.OPTIONAL:
                continue
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", workflow,
                          f"{name} 을(를) 워크플로가 넘겨 주지 않습니다")


class DigestTest(unittest.TestCase):
    """확인은 계속 쌓고, 메일은 정해 둔 요일·시각에만 한 번."""

    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        config_mod.save(path, {
            "recipients": ["team@example.com"],
            "email": {"enabled": True, "smtp_host": "smtp.example", "username": "u@example.com"},
            "digest": {"enabled": True, "days": ["mon", "tue", "wed", "thu", "fri"],
                       "times": ["08:00"], "timezone": "Asia/Seoul"},
            "sites": [{"id": "s1", "name": "테스트", "url": "https://example.com/jobs"}],
        })
        self.monitor = Monitor(path)
        self.sent = []
        original = notify_mod.send
        notify_mod.send = lambda _c, to, subject, text, _h="": self.sent.append((subject, text))
        self.addCleanup(lambda: setattr(notify_mod, "send", original))

    def _serve(self, items):
        result = extract_mod.ExtractResult(items=items, method="links", fingerprint="fp")
        self.monitor.collect = lambda _cfg, site: (
            FetchResult(url=site["url"], status=200, text="<html></html>",
                        content_type="text/html"), result)

    @staticmethod
    def _item(n):
        return {"id": f"i{n}", "title": f"공고 {n}", "url": f"https://example.com/{n}", "date": ""}

    def test_findings_are_held_until_the_send_time(self):
        self._serve([self._item(1)])
        self.monitor.run_check(notify=True)                   # 첫 확인: 기준만 저장

        # 아직 발송 시각이 지나지 않은 것으로 두면, 발견해도 메일은 나가지 않는다
        state = self.monitor.load_state()
        store_mod.outbox(state)["last_sent"] = store_mod.now_iso()
        self.monitor.save_state(state)

        self._serve([self._item(1), self._item(2), self._item(3)])
        summary = self.monitor.run_check(notify=True)
        self.assertEqual(summary["new_total"], 2)
        self.assertEqual(self.sent, [])
        self.assertEqual(summary["pending"]["items"], 2)
        self.assertIn("모아 두는 중", summary["email"]["skipped"])

        # 발송 시각이 지난 상태가 되면, 그동안 모인 것이 한 통으로 나간다
        state = self.monitor.load_state()
        store_mod.outbox(state)["last_sent"] = "2020-01-01T00:00:00+09:00"
        self.monitor.save_state(state)
        self._serve([self._item(1), self._item(2), self._item(3), self._item(4)])
        summary = self.monitor.run_check(notify=True)
        self.assertEqual(len(self.sent), 1)
        subject, text = self.sent[0]
        self.assertIn("새 공고 3건", subject)                   # 2건(모아 둔 것) + 1건(방금)
        for n in (2, 3, 4):
            self.assertIn(f"공고 {n}", text)
        self.assertEqual(summary["pending"]["items"], 0)        # 보냈으니 비워진다

    def test_send_window(self):
        cfg = self.monitor.load_config()
        kst = timezone(timedelta(hours=9))
        friday_9am = datetime(2026, 9, 11, 9, 0, tzinfo=kst)
        thursday_8am = datetime(2026, 9, 10, 8, 0, tzinfo=kst)
        # 목요일 8시에 보냈다면 금요일 9시에는 또 보낸다
        self.assertTrue(digest_mod.should_send(cfg, thursday_8am, friday_9am))
        # 금요일 8시에 보냈다면 같은 날 9시에는 보내지 않는다
        friday_8am = datetime(2026, 9, 11, 8, 0, tzinfo=kst)
        self.assertFalse(digest_mod.should_send(cfg, friday_8am, friday_9am))
        # 주말에는 발송 시각이 없으므로 금요일 발송 이후로는 조용하다
        saturday = datetime(2026, 9, 12, 12, 0, tzinfo=kst)
        self.assertFalse(digest_mod.should_send(cfg, friday_8am, saturday))
        # 월요일 아침이 되면 다시 나간다
        monday = datetime(2026, 9, 14, 8, 30, tzinfo=kst)
        self.assertTrue(digest_mod.should_send(cfg, friday_8am, monday))

    def test_partial_failure_does_not_resend_what_already_went_out(self):
        """공고 메일은 나갔는데 점검 안내가 실패하면, 다음 확인 때 공고 메일이 또 나가면 안 된다."""
        state = self.monitor.load_state()
        box = store_mod.outbox(state)
        box["items"] = [{"site_id": "s1", "site_name": "테스트", "site_link": "u",
                         "title": "공고 1", "url": "u1", "date": "", "found_at": "2026-09-09T00:00:00+09:00"}]
        box["alerts"] = [{"site_name": "테스트", "site_url": "u", "site_link": "u", "kind": "no_items",
                          "label": "목록을 못 읽었습니다", "detail": "0건", "recovered": False}]
        box["last_sent"] = "2020-01-01T00:00:00+09:00"     # 발송 시각이 지난 상태
        self.monitor.save_state(state)

        def flaky(_cfg, to, subject, text, _html=""):
            if "점검" in subject:
                raise notify_mod.NotifyError("SMTP 오류")
            self.sent.append((subject, text))
        original = notify_mod.send
        notify_mod.send = flaky
        self.addCleanup(lambda: setattr(notify_mod, "send", original))

        self._serve([])
        summary = self.monitor.run_check(notify=True)
        self.assertEqual(len(self.sent), 1)                 # 공고 메일만 나갔다
        self.assertIn("점검 안내", summary["email"]["error"])
        box = store_mod.outbox(self.monitor.load_state())
        self.assertEqual(box["items"], [])                  # 나간 것은 비워졌고
        self.assertEqual(len(box["alerts"]), 1)             # 실패한 것만 남는다
        self.assertEqual(box["last_sent"], "2020-01-01T00:00:00+09:00")   # 아직 다 못 보냈다

        notify_mod.send = lambda _c, to, subject, text, _h="": self.sent.append((subject, text))
        self.monitor.run_check(notify=True)                 # 다음 확인
        self.assertEqual(len(self.sent), 2)                 # 점검 안내만 추가로 나간다
        self.assertIn("점검", self.sent[1][0])
        self.assertEqual(store_mod.outbox(self.monitor.load_state())["alerts"], [])

    def test_disabled_digest_sends_right_away(self):
        cfg = self.monitor.load_config()
        cfg["digest"]["enabled"] = False
        self.monitor.save_config(cfg)
        self._serve([self._item(1)])
        self.monitor.run_check(notify=True)
        self._serve([self._item(1), self._item(2)])
        self.monitor.run_check(notify=True)
        self.assertEqual(len(self.sent), 1)


class ArchiveTest(unittest.TestCase):
    """설정 화면 '공고 이력' 탭이 읽는 목록."""

    def test_findings_are_kept_and_capped(self):
        state = {"sites": {}}
        results = [{"site_id": "s1", "site_name": "테스트", "site_link": "https://example.com/jobs",
                    "new_items": [{"title": "공고 A", "url": "https://example.com/a", "date": "2026-09-30"}]}]
        store_mod.hold(state, results, [], "2026-09-09T00:00:00+00:00")
        log = store_mod.archive(state)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["title"], "공고 A")
        self.assertEqual(log[0]["site_name"], "테스트")
        self.assertEqual(log[0]["found_at"], "2026-09-09T00:00:00+00:00")

        many = [{"site_id": "s1", "site_name": "테스트", "site_link": "u",
                 "new_items": [{"title": f"공고 {i}", "url": f"u{i}", "date": ""}
                               for i in range(store_mod.MAX_ARCHIVE + 20)]}]
        store_mod.hold(state, many, [], "2026-09-09T01:00:00+00:00")
        self.assertEqual(len(store_mod.archive(state)), store_mod.MAX_ARCHIVE)
        self.assertEqual(store_mod.archive(state)[-1]["title"],
                         f"공고 {store_mod.MAX_ARCHIVE + 19}")      # 최신이 남는다

    def test_backfill_fills_from_what_is_on_the_sites_now(self):
        """지금 올라와 있는 공고를 메일 없이 이력에만 채워 넣는다."""
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        config_mod.save(path, {"sites": [
            {"id": "s1", "name": "테스트", "url": "https://example.com/jobs"}]})
        monitor = Monitor(path)
        items = [{"id": f"i{i}", "title": f"공고 {i}", "url": f"https://example.com/{i}", "date": ""}
                 for i in range(3)]
        monitor.collect = lambda _cfg, site: (
            FetchResult(url=site["url"], status=200, text="<html></html>", content_type="text/html"),
            extract_mod.ExtractResult(items=items, method="links", fingerprint="fp"))

        # 이미 기억하고 있는 공고에는 첫 발견 시각이 있으니 그것을 쓴다
        state = monitor.load_state()
        entry = store_mod.site_state(state, "s1")
        entry["seen"] = {"i0": {"title": "공고 0", "url": "https://example.com/0", "date": "",
                                "first_seen": "2026-09-01T00:00:00+00:00"}}
        monitor.save_state(state)

        summary = monitor.backfill()
        self.assertEqual(summary["added"], 3)
        log = store_mod.archive(monitor.load_state())
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["found_at"], "2026-09-01T00:00:00+00:00")   # 기억해 둔 시각
        self.assertEqual(log[0]["site_name"], "테스트")

        self.assertEqual(monitor.backfill()["added"], 0)                    # 두 번 해도 안 늘어난다

    def test_seed_from_old_history_runs_once(self):
        state = {"sites": {"s1": {"history": [
            {"at": "2026-09-08T00:00:00+00:00", "new": [{"title": "옛 공고", "url": "u", "date": ""}]},
        ]}}}
        store_mod.seed_archive(state, {"s1": "테스트"})
        self.assertEqual([row["title"] for row in store_mod.archive(state)], ["옛 공고"])
        store_mod.seed_archive(state, {"s1": "테스트"})              # 두 번 불러도 늘지 않는다
        self.assertEqual(len(store_mod.archive(state)), 1)

    def test_mail_links_to_the_history_page(self):
        results = [{"site_name": "A", "site_url": "u", "site_link": "u", "status": "new",
                    "new_items": [{"title": "공고", "url": "https://example.com/1", "date": ""}]}]
        page = "https://example.github.io/x/monitor/"
        _subject, text, html = notify_mod.render(results, page_url=page)
        self.assertIn(page, text)
        # 꼬리말의 '채용공고 모니터' 가 이력 페이지로 이어진다
        self.assertIn(f"<a href='{page}' style='color:#0969da'>채용공고 모니터</a>", html)

        _s2, _t2, plain = notify_mod.render(results)      # 주소를 모르면 링크 없이
        self.assertIn("채용공고 모니터가 자동으로 보낸", plain)
        self.assertNotIn("<a href='https://example.github.io", plain)


class AccessGateTest(unittest.TestCase):
    """설정 화면 비밀번호 (해시만 저장한다)."""

    def test_password_is_never_stored_in_plain(self):
        cfg = config_mod.normalize({"access": {
            "enabled": True, "salt": "a1b2", "hash": "deadbeef", "hint": " 팀 이름 ",
        }})
        self.assertEqual(cfg["access"]["hint"], "팀 이름")
        self.assertNotIn("password", cfg["access"])
        text = json.dumps(cfg, ensure_ascii=False)
        self.assertIn("deadbeef", text)

    def test_cannot_turn_on_without_a_password(self):
        cfg = config_mod.normalize({"access": {"enabled": True}})
        self.assertFalse(cfg["access"]["enabled"])

if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- GitHub Actions 경로

class EnvSecretsTest(unittest.TestCase):
    """시크릿(환경변수)이 설정 파일보다 우선하는지."""

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in list(config_mod.ENV_OVERRIDES) +
                      ["JOBMON_RECIPIENTS", "JOBMON_EMAIL_ENABLED", "JOBMON_SMTP_PASSWORD"]}
        for key in self.saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_env_overrides_config(self):
        cfg = config_mod.normalize({"email": {"smtp_host": "old.example", "enabled": False},
                                    "recipients": ["old@example.com"]})
        os.environ["JOBMON_SMTP_HOST"] = "new.example"
        os.environ["JOBMON_RECIPIENTS"] = "a@example.com, b@example.com"
        os.environ["JOBMON_SMTP_PASSWORD"] = "비밀"
        live = config_mod.effective(cfg)
        self.assertEqual(live["email"]["smtp_host"], "new.example")
        # 새 공고는 설정 화면 수신처와 관리자 모두에게 간다
        self.assertEqual(live["recipients"],
                         ["old@example.com", "a@example.com", "b@example.com"])
        # 점검 안내는 시크릿에 적힌 관리자에게만 간다
        self.assertEqual(live["admin_recipients"], ["a@example.com", "b@example.com"])
        # 시크릿이 갖춰지면 별도 설정 없이도 메일 발송이 켜진다
        self.assertTrue(live["email"]["enabled"])
        # 원본 설정은 그대로 (파일에 비밀 정보가 다시 저장되지 않도록)
        self.assertEqual(cfg["email"]["smtp_host"], "old.example")

    def test_admin_falls_back_to_page_recipients(self):
        """시크릿이 없으면 설정 화면 수신처가 관리자 역할까지 한다."""
        live = config_mod.effective(config_mod.normalize({"recipients": ["me@example.com"]}))
        self.assertEqual(live["recipients"], ["me@example.com"])
        self.assertEqual(live["admin_recipients"], ["me@example.com"])

    def test_admin_addresses_are_never_saved(self):
        """시크릿에서 온 주소가 config.json 에 남으면 안 된다 (공개 저장소)."""
        os.environ["JOBMON_RECIPIENTS"] = "admin@example.com"
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        live = config_mod.effective(config_mod.normalize({"recipients": ["team@example.com"]}))
        config_mod.save(path, live)
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertNotIn("admin_recipients", saved)
        self.assertIn("admin@example.com", live["recipients"])   # 보낼 때는 쓰인다

    def test_env_summary_hides_values(self):
        os.environ["JOBMON_SMTP_PASSWORD"] = "비밀"
        summary = config_mod.env_summary()
        self.assertTrue(summary["JOBMON_SMTP_PASSWORD"])
        self.assertFalse(summary["JOBMON_SMTP_USER"])
        self.assertNotIn("비밀", json.dumps(summary, ensure_ascii=False))


class _FakeSMTPServer(threading.Thread):
    """테스트용 최소 SMTP 서버. 받은 메일 원문을 보관한다."""

    def __init__(self):
        super().__init__(daemon=True)
        import socket
        self.sock = socket.socket()
        self.sock.setsockopt(1, 2, 1)  # SOL_SOCKET, SO_REUSEADDR
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.messages = []

    def run(self):
        try:
            conn, _addr = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.sendall(b"220 test ESMTP\r\n")
            buf, in_data, message = b"", False, []
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    if in_data:
                        if line == b".":
                            in_data = False
                            self.messages.append(b"\r\n".join(message).decode("utf-8", "replace"))
                            message = []
                            conn.sendall(b"250 OK\r\n")
                        else:
                            message.append(line)
                        continue
                    upper = line.upper()
                    if upper.startswith(b"EHLO") or upper.startswith(b"HELO"):
                        conn.sendall(b"250-test\r\n250 SIZE 10240000\r\n")
                    elif upper.startswith(b"DATA"):
                        in_data = True
                        conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    elif upper.startswith(b"QUIT"):
                        conn.sendall(b"221 Bye\r\n")
                        return
                    else:
                        conn.sendall(b"250 OK\r\n")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class EmailDeliveryTest(unittest.TestCase):
    """새 공고가 생기면 실제로 SMTP 로 메일이 나가는지 (시크릿 경로 포함)."""

    def setUp(self):
        self.smtp = _FakeSMTPServer()
        self.smtp.start()
        self.addCleanup(self.smtp.close)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
        _SiteHandler.items = ['<li><a href="/jobs/1">첫 공고</a></li>']
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "config.json")
        config_mod.save(path, {
            "notify_on_first_run": True,
            "sites": [{"id": "local", "name": "테스트", "url": f"http://127.0.0.1:{self.httpd.server_address[1]}/"}],
        })
        self.monitor = Monitor(path)

        self.saved = {k: os.environ.get(k) for k in
                      ("JOBMON_SMTP_HOST", "JOBMON_SMTP_PORT", "JOBMON_SMTP_SECURITY",
                       "JOBMON_SMTP_FROM", "JOBMON_RECIPIENTS", "JOBMON_SMTP_PASSWORD")}
        os.environ.update({
            "JOBMON_SMTP_HOST": "127.0.0.1",
            "JOBMON_SMTP_PORT": str(self.smtp.port),
            "JOBMON_SMTP_SECURITY": "none",
            "JOBMON_SMTP_FROM": "bot@example.com",
            "JOBMON_RECIPIENTS": "to@example.com",
        })
        os.environ.pop("JOBMON_SMTP_PASSWORD", None)

        def restore():
            for key, value in self.saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)

    def test_new_posting_sends_mail(self):
        summary = self.monitor.run_check(notify=True)
        self.assertEqual(summary["new_total"], 1)
        self.assertTrue(summary["email"]["sent"], summary["email"]["error"])
        self.smtp.join(timeout=5)
        self.assertEqual(len(self.smtp.messages), 1)
        raw = self.smtp.messages[0]
        self.assertIn("Subject:", raw)
        self.assertIn("to@example.com", raw)
        self.assertIn("=?utf-8?", raw.lower())   # 한글 제목은 인코딩되어 나간다


class WorkflowHelpersTest(unittest.TestCase):
    """워크플로에서 쓰는 도우미들."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        sys.path.insert(0, BASE_DIR)

    def test_summary_json_written(self):
        import monitor as monitor_cli
        summary = {
            "at": "2026-09-07T10:00:00+09:00", "checked": 1, "new_total": 1,
            "email": {"sent": True, "error": "", "skipped": ""},
            "results": [{
                "site_id": "a", "site_name": "A", "site_url": "https://a.example", "status": "new",
                "item_count": 3, "method": "links", "note": "", "error": "",
                "new_items": [{"title": "새 공고", "url": "https://a.example/1", "date": "", "id": "x"}],
            }],
        }
        path = os.path.join(self.tmp.name, "data", "last-run.json")
        monitor_cli._write_summary_json(path, summary)
        with open(path, encoding="utf-8") as fh:
            written = json.load(fh)
        self.assertEqual(written["new_total"], 1)
        self.assertEqual(written["results"][0]["new_items"][0]["title"], "새 공고")
        self.assertNotIn("id", written["results"][0]["new_items"][0])

    def test_verify_config_blocks_password_in_repo(self):
        sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
        import verify_config

        clean = os.path.join(self.tmp.name, "clean.json")
        config_mod.save(clean, {"sites": [{"url": "https://a.example", "name": "A", "mode": "browser"}]})
        output = os.path.join(self.tmp.name, "gh-output.txt")
        os.environ["GITHUB_OUTPUT"] = output
        self.addCleanup(lambda: os.environ.pop("GITHUB_OUTPUT", None))
        self.assertEqual(verify_config.main(["verify_config.py", clean]), 0)
        with open(output, encoding="utf-8") as fh:
            self.assertIn("needs_browser=true", fh.read())

        leaked = os.path.join(self.tmp.name, "leaked.json")
        config_mod.save(leaked, {"email": {"password": "비밀"},
                                 "sites": [{"url": "https://a.example", "name": "A"}]})
        self.assertEqual(verify_config.main(["verify_config.py", leaked]), 1)

    def test_missing_config_is_reported(self):
        sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
        import verify_config
        self.assertEqual(verify_config.main(["verify_config.py", os.path.join(self.tmp.name, "없음.json")]), 1)


class DueSchedulingTest(unittest.TestCase):
    """한 번도 확인하지 않은 사이트는 즉시 확인 대상이어야 한다."""

    def test_never_checked_is_due(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        config_mod.save(path, {"sites": [{"id": "a", "name": "A", "url": "https://a.example"}]})
        monitor = Monitor(path)
        cfg, state = monitor.load_config(), monitor.load_state()
        self.assertIsNone(monitor.next_due(cfg, state, cfg["sites"][0]))
        self.assertEqual([s["id"] for s in monitor.due_sites(cfg, state)], ["a"])


class RetryTest(unittest.TestCase):
    """일시적인 접속 실패는 몇 번 더 시도한다."""

    def setUp(self):
        self.original = fetch_mod.RETRY_WAIT_SEC
        fetch_mod.RETRY_WAIT_SEC = (0, 0)
        self.addCleanup(lambda: setattr(fetch_mod, "RETRY_WAIT_SEC", self.original))

    def test_connection_error_is_retried(self):
        with self.assertRaises(fetch_mod.FetchError) as ctx:
            fetch_mod.fetch("http://127.0.0.1:9/none", timeout=1)
        self.assertIn(f"{fetch_mod.RETRY_ATTEMPTS}회 시도", str(ctx.exception))

    def test_succeeds_after_transient_failure(self):
        calls = {"n": 0}
        real = fetch_mod._fetch_once

        def flaky(url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise fetch_mod.FetchError("응답 시간 초과 (20초)", retryable=True)
            return FetchResult(url=url, status=200, text="<html></html>", content_type="text/html")

        fetch_mod._fetch_once = flaky
        self.addCleanup(lambda: setattr(fetch_mod, "_fetch_once", real))
        result = fetch_mod.fetch("https://example.com")
        self.assertEqual(result.status, 200)
        self.assertEqual(calls["n"], 2)

    def test_client_error_is_not_retried(self):
        calls = {"n": 0}
        real = fetch_mod._fetch_once

        def not_found(url, **kwargs):
            calls["n"] += 1
            raise fetch_mod.FetchError("HTTP 404 Not Found", retryable=False)

        fetch_mod._fetch_once = not_found
        self.addCleanup(lambda: setattr(fetch_mod, "_fetch_once", real))
        with self.assertRaises(fetch_mod.FetchError):
            fetch_mod.fetch("https://example.com")
        self.assertEqual(calls["n"], 1)


class HealthAlertTest(unittest.TestCase):
    """사이트가 조용히 망가졌을 때 점검 알림이 나가는지."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "config.json")
        config_mod.save(self.path, {"sites": [{"id": "a", "name": "A", "url": "https://a.example"}]})
        self.monitor = Monitor(self.path)
        self.cfg = self.monitor.load_config()
        self.site = self.cfg["sites"][0]
        self.state = store_mod.empty_state()
        self.entry = store_mod.site_state(self.state, "a")

    def _record(self, count, method="links", status="ok"):
        """확인 한 번을 흉내 내고 (알림, ) 을 돌려준다."""
        at = store_mod.now_iso()
        before = {"method": self.entry.get("last_method") or "",
                  "counts": store_mod.recent_item_counts(self.entry)}
        result = {"site_id": "a", "site_name": "A", "site_url": "https://a.example",
                  "site_link": "https://a.example", "status": status, "new_items": [],
                  "item_count": count, "method": method, "note": "", "error": "실패" if status == "error" else ""}
        store_mod.record_check(self.entry, at=at, status=status, method=method,
                               item_count=count, error=result["error"])
        self.entry["baseline_done"] = True
        return self.monitor.update_health(self.cfg, self.site, self.entry, result, at, before)

    def test_item_count_collapse_alerts_once_and_recovers(self):
        for _ in range(4):
            self.assertIsNone(self._record(40))
        alert = self._record(0)                      # 갑자기 하나도 못 읽음
        self.assertIsNotNone(alert)
        self.assertEqual(alert["kind"], "no_items")
        self.assertFalse(alert["recovered"])
        self.assertIsNone(self._record(0))           # 같은 문제로 또 보내지 않는다
        recovered = self._record(40)                 # 정상으로 돌아오면 복구 알림
        self.assertIsNotNone(recovered)
        self.assertTrue(recovered["recovered"])

    def test_big_drop_alerts(self):
        for _ in range(4):
            self._record(40)
        alert = self._record(10)
        self.assertEqual(alert["kind"], "drop")
        self.assertIn("40", alert["detail"])

    def test_method_change_alerts(self):
        for _ in range(3):
            self._record(30, method="json")
        alert = self._record(30, method="links")
        self.assertEqual(alert["kind"], "method_changed")

    def test_consecutive_errors_alert(self):
        self._record(30)
        self.assertIsNone(self._record(0, status="error"))
        self.assertIsNone(self._record(0, status="error"))
        alert = self._record(0, status="error")      # 기본값 3회째
        self.assertEqual(alert["kind"], "errors")

    def test_fingerprint_alerts_immediately(self):
        alert = self._record(0, method="fingerprint")
        self.assertEqual(alert["kind"], "fingerprint")

    def test_alerts_can_be_turned_off(self):
        self.cfg["alerts"]["enabled"] = False
        for _ in range(4):
            self._record(40)
        self.assertIsNone(self._record(0))

    def test_alert_is_mailed_even_without_new_postings(self):
        results = [{"site_name": "A", "site_url": "https://a.example", "site_link": "https://a.example",
                    "status": "ok", "new_items": []}]
        alerts = [{"site_id": "a", "site_name": "A", "site_url": "https://a.example",
                   "site_link": "https://a.example", "kind": "no_items",
                   "label": "목록을 하나도 읽지 못했습니다", "detail": "평소 40건", "recovered": False}]
        subject, text, html = notify_mod.render_alerts(alerts, results)
        self.assertIn("점검 필요", subject)
        self.assertIn("목록을 하나도 읽지 못했습니다", text)
        self.assertIn("평소 40건", html)


class SiteLinkTest(unittest.TestCase):
    """API 주소를 감시할 때 메일 링크는 사람이 볼 주소로 나가야 한다."""

    def test_site_link_prefers_home_url(self):
        cfg = config_mod.normalize({"sites": [{
            "name": "A", "url": "https://api.example.com/list", "home_url": "www.example.com/jobs",
        }]})
        site = cfg["sites"][0]
        self.assertEqual(config_mod.site_link(site), "https://www.example.com/jobs")

    def test_mail_uses_site_link(self):
        results = [{"site_name": "A", "site_url": "https://api.example.com/list",
                    "site_link": "https://www.example.com/jobs", "status": "new",
                    "new_items": [{"title": "공고", "url": "", "date": ""}]}]
        _subject, text, html = notify_mod.render(results)
        self.assertIn("https://www.example.com/jobs", text)
        self.assertNotIn("api.example.com", html)


class StableIdentityTest(unittest.TestCase):
    """제목에 매일 바뀌는 값이 섞여도 같은 공고로 인식해야 한다."""

    def _items(self, html, site=None):
        site = site or config_mod.normalize({"sites": [{"name": "A", "url": "https://a.example/list"}]})["sites"][0]
        return extract_mod.extract(site, fake(html, url="https://a.example/list")).items

    def test_same_posting_with_changing_dday(self):
        today = self._items('<a href="/o/1">간호사 채용 · 마감 D-114</a><a href="/o/2">설계 엔지니어 · D-3</a>')
        tomorrow = self._items('<a href="/o/1">간호사 채용 · 마감 D-113</a><a href="/o/2">설계 엔지니어 · D-2</a>')
        self.assertEqual([i["id"] for i in today], [i["id"] for i in tomorrow])

    def test_title_still_counts_when_urls_repeat(self):
        # 상세 주소가 없어 모두 같은 주소를 가리키면 제목으로 구분해야 한다
        items = self._items('<a href="/list">공고 A</a><a href="/list">공고 B</a>')
        self.assertEqual(len({i["id"] for i in items}), 2)

    def test_new_posting_is_still_detected(self):
        today = self._items('<a href="/o/1">간호사 채용 D-114</a>')
        tomorrow = self._items('<a href="/o/1">간호사 채용 D-113</a><a href="/o/9">신규 공고 D-30</a>')
        self.assertEqual(len(set(i["id"] for i in tomorrow) - set(i["id"] for i in today)), 1)

    def test_long_card_text_is_trimmed(self):
        items = self._items('<a href="/o/1">' + ("가" * 400) + "</a>")
        self.assertLessEqual(len(items[0]["title"]), 160)
