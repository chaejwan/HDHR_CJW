#!/usr/bin/env python3
"""설정 파일 점검 (GitHub Actions 첫 단계에서 실행).

- JSON 형식이 맞는지, 사이트 주소가 있는지 확인한다.
- 저장소에 SMTP 비밀번호가 실려 있으면 즉시 실패시킨다 (공개 저장소 사고 방지).
- 브라우저 렌더링(mode=browser)이 필요한 사이트가 있는지 워크플로에 알려 준다.
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from jobmon import config as config_mod  # noqa: E402


def emit_output(name: str, value: str) -> None:
    """워크플로 다음 단계에서 쓸 수 있게 값을 넘긴다."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


def main(argv) -> int:
    path = argv[1] if len(argv) > 1 else os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(path):
        print(f"설정 파일이 없습니다: {path}")
        return 1
    try:
        cfg = config_mod.load(path, create_if_missing=False)
    except FileNotFoundError:
        print(f"설정 파일이 없습니다: {path}")
        return 1
    except ValueError as exc:
        print(f"설정 파일을 읽을 수 없습니다: {exc}")
        return 1

    if (cfg.get("email") or {}).get("password"):
        print("설정 파일에 SMTP 비밀번호가 들어 있습니다. "
              "공개 저장소에 비밀번호를 두면 안 됩니다.\n"
              "config.json 의 email.password 를 비우고, 저장소 Secrets 의 "
              "JOBMON_SMTP_PASSWORD 로 옮기세요.")
        return 1

    sites = cfg.get("sites") or []
    enabled = [s for s in sites if s.get("enabled")]
    needs_browser = any(s.get("mode") == "browser" for s in enabled)

    print(f"사이트 {len(enabled)}/{len(sites)}곳 사용 중 · 기본 주기 {cfg['check_interval_hours']}시간")
    for site in sites:
        mark = "○" if site.get("enabled") else "×"
        print(f"  {mark} [{site['id']}] {site['name']} · {site['mode']} · {site['url']}")
    # 수신처가 어디서 몇 명 잡히는지 (주소는 찍지 않는다). 시크릿을 워크플로에서
    # 넘겨 주는 것을 빠뜨리면 여기 숫자가 0 으로 나와 바로 알아볼 수 있다.
    from jobmon import digest as digest_mod
    if (cfg.get("digest") or {}).get("enabled"):
        print(f"메일 발송 시각 · {digest_mod.describe(cfg)} (그 사이 발견한 공고는 모아 둡니다)")
    else:
        print("메일 발송 · 새 공고를 발견하는 즉시")

    live = config_mod.effective(cfg)
    page = len(cfg.get("recipients") or [])
    print(f"수신처 · 새 공고 {len(live.get('recipients') or [])}명"
          f"(설정 화면 {page}명 + 시크릿 {len(live.get('recipients') or []) - page}명)"
          f" · 점검 안내 {len(live.get('admin_recipients') or [])}명")
    for warning in config_mod.validate(cfg):
        print(f"  알림: {warning}")
    if not enabled:
        print("확인할 사이트가 없습니다. 설정 페이지에서 사이트를 추가하세요.")

    emit_output("needs_browser", "true" if needs_browser else "false")
    emit_output("site_count", str(len(enabled)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
