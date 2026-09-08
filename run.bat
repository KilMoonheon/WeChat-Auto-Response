@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.13 -m pip install -r requirements.txt -q
py -3.13 main.py
pause
