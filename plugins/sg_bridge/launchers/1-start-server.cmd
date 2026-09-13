@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ==========================================================
echo  SG-Bridge HTTP server  -^>  http://127.0.0.1:8765/docs
echo  Keep this window OPEN. Close it to stop the server.
echo ==========================================================
python -X utf8 -m sg_bridge.cli serve --port 8765
pause
