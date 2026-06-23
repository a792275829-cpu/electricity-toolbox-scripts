@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0private-data-uploader.ps1"
if errorlevel 1 pause
