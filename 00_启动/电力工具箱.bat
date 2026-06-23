@echo off
setlocal

set "LAUNCHER_DIR=%~dp0"
set "PREFERRED_PYTHONW=C:\Users\lllg\AppData\Local\Programs\Python\Python311\pythonw.exe"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$roots = New-Object System.Collections.Generic.List[string];" ^
  "foreach($item in @($env:LAUNCHER_DIR, (Split-Path -Parent $env:LAUNCHER_DIR), (Get-Location).Path)) { if($item -and (Test-Path -LiteralPath $item -PathType Container)) { [void]$roots.Add([IO.Path]::GetFullPath($item)) } }" ^
  "$script = $null;" ^
  "foreach($root in $roots) { $candidate = Join-Path $root 'toolbox_launcher.pyw'; $app = Join-Path $root 'toolbox\app.py'; if((Test-Path -LiteralPath $candidate -PathType Leaf) -and (Test-Path -LiteralPath $app -PathType Leaf)) { $script = (Resolve-Path -LiteralPath $candidate).Path; break }; $found = Get-ChildItem -LiteralPath $root -Filter 'toolbox_launcher.pyw' -File -Recurse -ErrorAction SilentlyContinue | Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'toolbox\app.py') -PathType Leaf } | Select-Object -First 1; if($found) { $script = $found.FullName; break } }" ^
  "if(-not $script) { foreach($base in @((Join-Path $env:USERPROFILE 'Desktop'), (Join-Path $env:USERPROFILE 'Documents'))) { if($script) { break }; if(Test-Path -LiteralPath $base -PathType Container) { $found = Get-ChildItem -LiteralPath $base -Filter 'toolbox_launcher.pyw' -File -Recurse -ErrorAction SilentlyContinue | Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'toolbox\app.py') -PathType Leaf } | Select-Object -First 1; if($found) { $script = $found.FullName } } } }" ^
  "if(-not $script) { Write-Host 'Toolbox launcher not found. Put this bat beside toolbox_launcher.pyw, inside 00_start, or keep the toolbox folder under Desktop/Documents.'; exit 1 }" ^
  "if($env:TOOLBOX_SMOKE -eq '1') { Write-Host ('TOOLBOX_SCRIPT=' + $script); exit 0 }" ^
  "$root = Split-Path -Parent $script;" ^
  "$workspace = Split-Path -Parent $root;" ^
  "$bundled = Join-Path $workspace 'runtime\python311\pythonw.exe';" ^
  "if(Test-Path -LiteralPath $bundled -PathType Leaf) { Start-Process -FilePath $bundled -ArgumentList @($script) -WorkingDirectory $root; exit 0 }" ^
  "$preferred = $env:PREFERRED_PYTHONW;" ^
  "if(Test-Path -LiteralPath $preferred -PathType Leaf) { Start-Process -FilePath $preferred -ArgumentList @($script) -WorkingDirectory $root; exit 0 }" ^
  "$pyw = Get-Command pyw -ErrorAction SilentlyContinue;" ^
  "if($pyw) { Start-Process -FilePath $pyw.Source -ArgumentList @('-3.11', $script) -WorkingDirectory $root; exit 0 }" ^
  "Write-Host 'Python 3.11 GUI launcher was not found.'; exit 1"

set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%
