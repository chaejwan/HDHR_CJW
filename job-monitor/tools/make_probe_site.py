#!/usr/bin/env python3
"""진단용 임시 사이트 설정을 만든다 (환경변수 → JSON 파일).

  PROBE_URL / PROBE_METHOD / PROBE_BODY 를 읽어 --site-json 으로 넘길 파일을 만든다.
"""

import json
import os
import sys


def main(argv) -> int:
    url = os.environ.get("PROBE_URL", "").strip()
    if not url:
        print("PROBE_URL 이 비어 있습니다.")
        return 1
    body = os.environ.get("PROBE_BODY", "")
    site = {
        "name": "probe",
        "url": url,
        "mode": "auto",
        "method": (os.environ.get("PROBE_METHOD") or ("POST" if body else "GET")).upper(),
        "body": body,
        "headers": {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
    }
    out = argv[1] if len(argv) > 1 else "/tmp/site.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(site, fh, ensure_ascii=False, indent=1)
    print(f"{site['method']} {url}" + (f" · 본문 {body[:80]}" if body else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
