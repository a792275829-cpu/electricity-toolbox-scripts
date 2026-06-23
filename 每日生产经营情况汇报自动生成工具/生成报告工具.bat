@echo off
setlocal
cd /d "%~dp0scripts"

set PY=C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

"%PY%" report_gui.py
if errorlevel 1 pause
