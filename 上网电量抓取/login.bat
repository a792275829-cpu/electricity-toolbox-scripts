@echo off
setlocal
cd /d "%~dp0"

set PY=C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

"%PY%" export_online_energy.py --login
pause
