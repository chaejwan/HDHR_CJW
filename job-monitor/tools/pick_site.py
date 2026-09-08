#!/usr/bin/env python3
"""설정에서 사이트 한 곳만 뽑아 파일로 저장한다 (진단 워크플로용).

  python3 tools/pick_site.py <config.json> <사이트id> <출력파일>
"""

import json
import sys


def main(argv) -> int:
    if len(argv) < 4:
        print("사용법: pick_site.py <config.json> <사이트id> <출력파일>")
        return 1
    config_path, site_id, out_path = argv[1], argv[2], argv[3]
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for site in cfg.get("sites") or []:
        if site.get("id") == site_id:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(site, fh, ensure_ascii=False, indent=1)
            print(f"[{site_id}] {site.get('name')} · {site.get('mode')} · {site.get('url')}")
            return 0
    print(f"설정에 '{site_id}' 사이트가 없습니다. 있는 id: "
          + ", ".join(s.get("id", "") for s in cfg.get("sites") or []))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
