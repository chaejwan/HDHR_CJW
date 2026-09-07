#!/usr/bin/env bash
# 더블클릭하거나 터미널에서 실행하면 설정 화면이 열립니다.
cd "$(dirname "$0")" || exit 1
exec python3 monitor.py serve --open
