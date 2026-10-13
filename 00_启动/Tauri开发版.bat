@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0.."
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
if "%TOOLBOX_SMOKE%"=="1" (
  echo TAURI_PROJECT=%ROOT%\desktop
  exit /b 0
)
where node >nul 2>nul || goto prerequisites
where cargo >nul 2>nul || goto prerequisites
if not exist "%ROOT%\.venv\Scripts\python.exe" goto prerequisites
cd /d "%ROOT%\desktop"
if not exist node_modules call npm ci || goto failed
if not exist src-tauri\payload mkdir src-tauri\payload
call npm run dev
goto :eof
:prerequisites
echo 请先按 desktop\README.md 配置 Node、Rust、VS C++ 和根目录 .venv。
echo 分发的正式安装版已内置运行环境，不需要这些开发依赖。
:failed
pause
exit /b 1
