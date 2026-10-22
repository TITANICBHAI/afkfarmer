@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo AFK Farmer - Microsoft Edge
echo ==========================================

if not exist "main.py" (
  echo ERROR: main.py was not found.
  echo Put this BAT file in the AFK Farmer project folder.
  pause
  exit /b 1
)

set "EDGE_EXE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE_EXE%" set "EDGE_EXE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE_EXE%" set "EDGE_EXE=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"

if not exist "%EDGE_EXE%" (
  echo ERROR: Could not find msedge.exe in the usual install locations.
  echo Edit EDGE_EXE in this BAT file and set the correct path.
  pause
  exit /b 1
)

rem Force this run to use Edge, without changing persistent Windows settings.
set "PLAYWRIGHT_EXECUTABLE_PATH=%EDGE_EXE%"
set "PLAYWRIGHT_CDP_URL="
set "PLAYWRIGHT_CDP_PORTS="
set "AFK_BROWSER_PROFILE_DIR=browser_profile_edge"
set "AFK_BROWSER_DEBUG_PORT=9333"

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found on PATH.
  echo Install Python or activate the environment used by this project.
  pause
  exit /b 1
)

python main.py
set "RUN_EXIT=%ERRORLEVEL%"
echo.
echo AFK Farmer exited with code %RUN_EXIT%.
pause
exit /b %RUN_EXIT%
