@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo Verifying all 4 games (launch / attach / key / read / close)...
python -X utf8 -m sg_bridge.tools.verify_games
pause
