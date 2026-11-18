@echo off
rem Double-click in File Explorer to install research brain, or to update it, and then open it.
rem (Windows may say "Windows protected your PC": click More info, then Run anyway.)
cd /d "%~dp0"
if not exist "%~dp0install.ps1" (
  echo The installer can't find its other files.
  echo.
  echo This usually means it was opened from inside the zip. Windows shows a zip like a folder, but the
  echo installer only works from an unzipped copy:
  echo   1. Close this window.
  echo   2. Right-click research-brain-installer-....zip and choose "Extract All...", then Extract.
  echo   3. In the folder that opens, double-click Install-research-brain.cmd again.
  echo.
  pause
  exit /b 2
)
echo Installing research brain.
echo The first time takes a few minutes (it downloads about 280 MB). Keep this window open.
echo If research brain is open, it closes now to be updated; your library is saved as you go.
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
