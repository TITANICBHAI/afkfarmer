@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AFK Farmer - Google Chrome

echo ==========================================
echo AFK Farmer - Google Chrome
echo ==========================================
echo.

if not exist "%~dp0main.py" goto MissingMain

rem Look for Chrome in common install locations. Keep these checks outside
rem parenthesized IF blocks so spaces and parentheses in paths cannot confuse CMD.
set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME_EXE%" goto ChromeFound

set "CHROME_EXE=%SystemDrive%\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if exist "%CHROME_EXE%" goto ChromeFound

set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME_EXE%" goto ChromeFound

set "CHROME_EXE="
for /f "delims=" %%I in ('where chrome.exe 2^>nul') do if not defined CHROME_EXE set "CHROME_EXE=%%I"
if not defined CHROME_EXE goto ChromeMissing
if not exist "%CHROME_EXE%" goto ChromeMissing

goto ChromeFound

:ChromeFound
echo Chrome: "%CHROME_EXE%"
echo Profile: browser_profile_chrome
echo Debug port: 9334
echo.

rem All settings below apply only to this run; nothing is saved with SETX.
set "PLAYWRIGHT_EXECUTABLE_PATH=%CHROME_EXE%"
set "PLAYWRIGHT_BROWSER_CHANNEL="
set "PLAYWRIGHT_CDP_PORTS="
rem Avoid attaching to a different browser instance; launch this copy's own Chrome.
set "PLAYWRIGHT_CDP_URL=http://127.0.0.1:1"
set "AFK_BROWSER_MODE=persistent"
set "AFK_BROWSER_PROFILE_DIR=browser_profile_chrome"
set "AFK_BROWSER_DEBUG_PORT=9334"
set "AFK_BROWSER_EXTRA_ARGS="

where python >nul 2>nul
if not errorlevel 1 goto RunWithPython
where py >nul 2>nul
if not errorlevel 1 goto RunWithPy

echo ERROR: Python was not found on PATH.
echo Install Python or open CMD from the project's configured Python environment.
echo.
pause
exit /b 1

:RunWithPython
python main.py
set "RUN_EXIT=%ERRORLEVEL%"
goto RunFinished

:RunWithPy
py main.py
set "RUN_EXIT=%ERRORLEVEL%"
goto RunFinished

:RunFinished
echo.
echo AFK Farmer exited with code %RUN_EXIT%.
pause
exit /b %RUN_EXIT%

:MissingMain
echo ERROR: main.py was not found in this folder.
echo Put run_chrome.bat beside main.py in the AFK Farmer project root.
echo.
pause
exit /b 1

:ChromeMissing
echo ERROR: Google Chrome executable was not found.
echo Checked common Chrome install locations and the PATH.
echo Install Chrome or edit CHROME_EXE in this BAT file to your chrome.exe path.
echo.
pause
exit /b 1
