@echo off
setlocal
cd /d "%~dp0scripts"

set PY=C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

"%PY%" fetch_daily_data.py --login
pause
