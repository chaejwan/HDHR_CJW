@echo off
REM 더블클릭하면 설정 화면이 열립니다.
cd /d "%~dp0"
python monitor.py serve --open
pause
