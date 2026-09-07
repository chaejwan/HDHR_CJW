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

from jobmon import config as config_mod       # noqa: E402
from jobmon import extract as extract_mod     # noqa: E402
from jobmon import notify as notify_mod       # noqa: E402
from jobmon import server as server_mod       # noqa: E402
from jobmon import store as store_mod         # noqa: E402
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

    def test_errors_are_reported(self):
        results = [
            {"site_name": "A", "site_url": "u", "status": "error", "error": "HTTP 500", "new_items": []},
            {"site_name": "B", "site_url": "u", "status": "new",
             "new_items": [{"title": "T", "url": "u", "date": ""}]},
        ]
        _subject, text, _html = notify_mod.render(results)
        self.assertIn("HTTP 500", text)


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


if __name__ == "__main__":
    unittest.main()
