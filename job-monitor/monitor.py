#!/usr/bin/env python3
"""채용공고 모니터 실행 파일.

  python3 monitor.py serve      설정 화면(웹 UI) + 주기 확인 함께 실행  ← 보통 이것만 쓰면 됩니다
  python3 monitor.py check      지금 한 번 확인 (cron/작업 스케줄러용)
  python3 monitor.py run        웹 UI 없이 주기 확인만 실행
  python3 monitor.py list       등록된 사이트와 마지막 확인 상태 보기
  python3 monitor.py diagnose <주소>   해당 주소에서 무엇이 추출되는지 점검
  python3 monitor.py test-email 메일 설정 점검용 테스트 메일 발송
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from jobmon import config as config_mod   # noqa: E402
from jobmon import extract as extract_mod  # noqa: E402
from jobmon import fetch as fetch_mod      # noqa: E402
from jobmon import notify as notify_mod    # noqa: E402
from jobmon.runner import Monitor          # noqa: E402
from jobmon import server as server_mod    # noqa: E402

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor.py",
        description="지정한 채용 사이트를 주기적으로 확인해 새 공고를 이메일로 알려 줍니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="설정 파일 경로")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="설정 웹 UI + 주기 확인 실행")
    serve.add_argument("--host", default="127.0.0.1", help="기본값 127.0.0.1 (내 컴퓨터에서만 접속)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-scheduler", action="store_true", help="주기 확인 없이 설정 화면만 띄우기")
    serve.add_argument("--open", action="store_true", dest="open_browser", help="브라우저를 자동으로 열기")

    check = sub.add_parser("check", help="지금 한 번 확인")
    check.add_argument("--site", action="append", dest="sites", help="사이트 id (여러 번 지정 가능)")
    check.add_argument("--due-only", action="store_true", help="확인할 때가 된 사이트만 (cron 으로 자주 돌릴 때)")
    check.add_argument("--no-email", action="store_true", help="메일을 보내지 않고 화면에만 출력")
    check.add_argument("--summary-json", help="확인 결과 요약을 이 경로에 JSON 으로 저장 (설정 페이지 표시용)")

    run = sub.add_parser("run", help="웹 UI 없이 주기 확인만 계속 실행")
    run.add_argument("--tick", type=int, default=30, help="예정 시각 확인 간격(초)")

    sub.add_parser("list", help="사이트 목록과 상태 보기")
    sub.add_parser("test-email", help="테스트 메일 보내기")

    diagnose = sub.add_parser("diagnose", help="주소에서 무엇이 추출되는지 점검")
    diagnose.add_argument("url", nargs="?", default="")
    diagnose.add_argument("--site-json", help="사이트 설정 JSON 파일로 점검 (url/mode/method/body/json 매핑)")
    diagnose.add_argument("--browser", action="store_true", help="Playwright 로 렌더링해서 확인")
    diagnose.add_argument("--network", action="store_true",
                          help="렌더링 중 오간 JSON 응답까지 살펴 목록 API 를 찾는다 (--browser 필요)")
    return parser


def _run_url() -> str:
    """GitHub Actions 안에서 실행 중이면 그 실행 기록 주소를 만든다."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def _write_summary_json(path: str, summary: dict) -> None:
    """설정 페이지에서 읽을 수 있도록 마지막 실행 결과를 남긴다."""
    payload = {
        "at": summary["at"],
        "checked": summary["checked"],
        "new_total": summary["new_total"],
        "email": summary["email"],
        "run_url": _run_url(),
        "results": [
            {
                "site_id": r["site_id"], "site_name": r["site_name"], "site_url": r["site_url"],
                "status": r["status"], "item_count": r["item_count"], "method": r["method"],
                "note": r["note"], "error": r["error"],
                "new_items": [
                    {"title": i.get("title", ""), "url": i.get("url", ""), "date": i.get("date", "")}
                    for i in r["new_items"]
                ],
            }
            for r in summary["results"]
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def _write_step_summary(summary: dict) -> None:
    """GitHub Actions 실행 화면에 결과를 표로 남긴다."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## 채용공고 확인 결과 — 새 공고 {summary['new_total']}건", "",
             "| 사이트 | 상태 | 항목 수 | 새 공고 | 비고 |", "| --- | --- | ---: | ---: | --- |"]
    for r in summary["results"]:
        note = r["error"] or r["note"] or ""
        lines.append(
            f"| [{r['site_name']}]({r['site_url']}) | {r['status']} | {r['item_count']} | "
            f"{len(r['new_items'])} | {note.replace('|', '/')} |"
        )
    for r in summary["results"]:
        if not r["new_items"]:
            continue
        lines += ["", f"### {r['site_name']}"]
        lines += [f"- [{i['title']}]({i['url']}){(' — ' + i['date']) if i.get('date') else ''}"
                  for i in r["new_items"]]
    mail = summary["email"]
    lines += ["", "메일: " + ("발송함" if mail["sent"] else (mail["error"] or mail["skipped"] or "보낼 새 공고 없음"))]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def cmd_check(monitor: Monitor, args) -> int:
    summary = monitor.run_check(
        site_ids=args.sites,
        notify=not args.no_email,
        only_due=args.due_only,
    )
    if getattr(args, "summary_json", None):
        _write_summary_json(args.summary_json, summary)
    _write_step_summary(summary)
    print()
    for result in summary["results"]:
        print(f"■ {result['site_name']} — {result['status']} (항목 {result['item_count']}개)")
        if result["error"]:
            print(f"   오류: {result['error']}")
        if result["note"]:
            print(f"   메모: {result['note']}")
        for item in result["new_items"]:
            print(f"   + {item['title']}")
            print(f"     {item['url']}")
    print(f"\n확인 {summary['checked']}곳 · 새 공고 {summary['new_total']}건")
    mail = summary["email"]
    if mail["sent"]:
        print("알림 메일을 보냈습니다.")
    elif mail["error"]:
        print(f"메일 발송 실패: {mail['error']}")
    elif mail["skipped"]:
        print(f"메일 발송 생략: {mail['skipped']}")
    return 1 if any(r["status"] == "error" for r in summary["results"]) else 0


def cmd_list(monitor: Monitor) -> int:
    cfg = monitor.load_config()
    state = monitor.load_state()
    if not cfg["sites"]:
        print("등록된 사이트가 없습니다. 'python3 monitor.py serve' 로 설정 화면을 열어 추가하세요.")
        return 0
    print(f"기본 확인 주기: {cfg['check_interval_hours']}시간 · "
          f"수신 이메일: {', '.join(cfg['recipients']) or '(없음)'}")
    for site in cfg["sites"]:
        entry = (state.get("sites") or {}).get(site["id"]) or {}
        print()
        print(f"[{site['id']}] {site['name']}{'' if site['enabled'] else '  (사용 안 함)'}")
        print(f"   {site['url']}")
        print(f"   주기 {config_mod.interval_hours(cfg, site)}시간 · 방식 {site['mode']}")
        print(f"   마지막 확인 {entry.get('last_check') or '없음'} "
              f"({entry.get('last_status') or '-'}) · 기억 중 {len(entry.get('seen') or {})}건")
        if entry.get("last_error"):
            print(f"   오류: {entry['last_error']}")
    return 0


def cmd_test_email(monitor: Monitor) -> int:
    cfg = config_mod.effective(monitor.load_config())
    recipients = cfg.get("recipients") or []
    if not recipients:
        print("수신 이메일이 설정돼 있지 않습니다.")
        return 1
    try:
        notify_mod.send_test(cfg["email"], recipients)
    except notify_mod.NotifyError as exc:
        print(f"실패: {exc}")
        return 1
    print(f"{', '.join(recipients)} 로 테스트 메일을 보냈습니다.")
    return 0


def _outline(data, path: str = "", depth: int = 0, out=None) -> list:
    """JSON 안의 배열 위치와 첫 항목의 필드 이름을 훑어 준다."""
    out = out if out is not None else []
    if depth > 6 or len(out) > 40:
        return out
    if isinstance(data, list):
        first = data[0] if data else None
        if isinstance(first, dict):
            out.append(f"      {path or '(최상위)'} : 배열 {len(data)}건 · 필드 {', '.join(list(first)[:14])}")
            sample = {k: v for k, v in list(first.items())[:6] if not isinstance(v, (dict, list))}
            out.append(f"        예시 : {json.dumps(sample, ensure_ascii=False)[:300]}")
            _outline(first, f"{path}[0]", depth + 1, out)
        elif data:
            out.append(f"      {path or '(최상위)'} : 배열 {len(data)}건 (값 목록)")
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                _outline(value, f"{path}.{key}" if path else key, depth + 1, out)
    return out


def _describe_json_capture(capture: dict) -> list:
    """렌더링 중 오간 JSON 응답 하나를 설명한다 (목록처럼 보이는 배열을 모두 표시)."""
    method = capture.get("method", "GET")
    lines = [f"· [{method}] {capture['url']} ({len(capture['text'])}자)"]
    if capture.get("post_data"):
        lines.append(f"    보낸 본문 : {capture['post_data'][:300]}")
    try:
        data = json.loads(capture["text"])
    except (ValueError, TypeError):
        lines.append("    (JSON 으로 읽지 못함)")
        return lines
    if isinstance(data, dict):
        lines.append(f"    최상위 키 : {', '.join(list(data)[:12])}")
    candidates = sorted(extract_mod.find_item_arrays(data), key=lambda pair: -len(pair[1]))[:3]
    if not candidates:
        lines.append("    목록처럼 보이는 배열 없음 — 구조 개요:")
        lines.extend(_outline(data))
        return lines
    for path, rows in candidates:
        items = [it for it in (extract_mod.item_from_object(r, capture["url"]) for r in rows) if it]
        if not items:
            continue
        sample = rows[0] if isinstance(rows[0], dict) else {}
        lines.append(f"    목록 후보 : {path or '(최상위 배열)'} · {len(items)}건 "
                     f"· 필드 {', '.join(list(sample)[:10])}")
        for item in items[:5]:
            lines.append(f"        - {item['title']}")
        lines.append(f"      설정 예시 : mode=json · url={capture['url']} · "
                     f"method={method} · items_path={path or ''}")
    return lines


def cmd_diagnose(monitor: Monitor, args) -> int:
    cfg = monitor.load_config()
    if getattr(args, "site_json", None):
        with open(args.site_json, encoding="utf-8") as fh:
            raw_site = json.load(fh)
        raw_site.setdefault("name", "diagnose")
        site = config_mod.normalize({"sites": [raw_site]})["sites"][0]
        args.url = site["url"]
        args.browser = site["mode"] == "browser"
    else:
        if not args.url:
            print("주소나 --site-json 중 하나는 지정해야 합니다.")
            return 1
        site = config_mod.normalize({"sites": [{
            "name": "diagnose", "url": args.url, "mode": "browser" if args.browser else "auto",
        }]})["sites"][0]
    request_cfg = cfg.get("request") or {}
    timeout = float(request_cfg.get("timeout_sec") or 20)
    user_agent = request_cfg.get("user_agent") or fetch_mod.DEFAULT_UA

    captured = []
    try:
        if args.browser:
            fetched = fetch_mod.fetch_rendered(
                site["url"], timeout=max(timeout, 30), user_agent=user_agent, capture=args.network)
            if args.network:
                fetched, captured = fetched
        else:
            fetched = fetch_mod.fetch(
                site["url"], timeout=timeout, user_agent=user_agent,
                headers=site.get("headers") or None,
                method=site.get("method") or "GET", body=site.get("body") or "")
    except fetch_mod.FetchError as exc:
        print(f"접속 실패: {exc}")
        return 1

    result = extract_mod.extract(site, fetched)
    raw_sample = ""
    if fetched.looks_json:
        try:
            data = json.loads(fetched.text)
            arrays = extract_mod.find_item_arrays(data)
            if arrays:
                _path, rows = max(arrays, key=lambda pair: len(pair[1]))
                if rows:
                    raw_sample = json.dumps(rows[0], ensure_ascii=False)[:800]
        except (ValueError, TypeError):
            pass
    lines = [
        f"주소       : {fetched.url}",
        f"응답       : HTTP {fetched.status} · {fetched.content_type or '-'}"
        f"{' · 렌더링됨' if fetched.rendered else ''} · {len(fetched.text)}자",
        f"추출 방식  : {result.method}",
        f"찾은 항목  : {len(result.items)}개",
    ]
    if result.note:
        lines.append(f"메모       : {result.note}")
    if raw_sample:
        lines.append(f"항목 원본  : {raw_sample}")
    for item in result.items[:20]:
        lines.append(f"  - {item['title']}")
        lines.append(f"    {item['url']}")

    if args.network:
        lines.append("")
        lines.append(f"주고받은 JSON 응답 {len(captured)}건:")
        for capture in captured:
            lines.extend(_describe_json_capture(capture))
            lines.append("")
    elif not result.items:
        lines.append("")
        if fetched.looks_json:
            try:
                lines.append("응답 구조 개요:")
                lines.extend(_outline(json.loads(fetched.text)))
            except (ValueError, TypeError):
                pass
        else:
            lines.append("항목을 찾지 못했습니다. 자바스크립트로 목록을 그리는 사이트라면 "
                         "--browser --network 로 다시 시도해 보세요.")

    text = "\n".join(lines)
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"## 사이트 진단 — {args.url}\n\n```\n{text}\n```\n")
        except OSError:
            pass
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    monitor = Monitor(args.config)

    if command == "serve":
        if getattr(args, "open_browser", False):
            import threading
            import webbrowser
            host = "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
            threading.Timer(1.0, webbrowser.open, args=(f"http://{host}:{args.port}",)).start()
        server_mod.serve(monitor, host=args.host, port=args.port,
                         with_scheduler=not args.no_scheduler)
        return 0
    if command == "check":
        return cmd_check(monitor, args)
    if command == "run":
        monitor.run_forever(tick_seconds=args.tick)
        return 0
    if command == "list":
        return cmd_list(monitor)
    if command == "test-email":
        return cmd_test_email(monitor)
    if command == "diagnose":
        return cmd_diagnose(monitor, args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
