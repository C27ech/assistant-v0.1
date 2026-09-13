@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo Running SG-Bridge self test (notepad as stand-in, ~10s)...
python -X utf8 -m sg_bridge.selftest
pause
