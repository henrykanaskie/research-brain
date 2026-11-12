@echo off
rem Double-click in File Explorer to install research brain, or to update it, and then open it.
rem (Windows may say "Windows protected your PC": click More info, then Run anyway.)
cd /d "%~dp0"
echo Installing research brain.
echo The first time takes a few minutes (it downloads about 280 MB). Keep this window open.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Something went wrong. What it said is above: a screenshot of this window helps whoever helps you.
  pause
  exit /b 1
)
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%PATH%;%%P"
echo.
echo Done. research brain is in your Start menu. Opening it now.
start "" research-brain open
echo.
pause
