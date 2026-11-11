@echo off
rem Double-click in File Explorer to start research_brain on Windows (installs it the first time).
cd /d "%~dp0"
where research-brain >nul 2>nul
if errorlevel 1 (
  echo Installing research_brain the first time...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  if errorlevel 1 ( pause & exit /b 1 )
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%PATH%;%%P"
)
research-brain
if errorlevel 1 pause
