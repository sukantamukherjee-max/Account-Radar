@echo off
REM ============================================================
REM  Account Radar - LIVE management demo launcher
REM  Same application and research engine as normal use; every
REM  "Analyze account" runs fresh research against current public
REM  sources. Nothing is replayed. Port 8502 (development: 8501).
REM ============================================================
setlocal
cd /d "%~dp0"

set "PYLAUNCHER=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
if not exist "%PYLAUNCHER%" set "PYLAUNCHER=py"

echo.
echo   ACCOUNT RADAR  ^|  LIVE DEMO MODE
echo   Current public-domain research - fresh analysis on every run
echo   Opening http://localhost:8502  (close this window to stop)
echo.

REM Search providers: Brave (primary) - Tavily - Serper (last fallback).
if "%BRAVE_SEARCH_API_KEY%%TAVILY_API_KEY%%SERPER_API_KEY%"=="" (
    echo   WARNING: no search provider key is set in this environment.
    echo   Set BRAVE_SEARCH_API_KEY, TAVILY_API_KEY or SERPER_API_KEY.
    echo   Live research will not run until one is configured.
    echo.
)

"%PYLAUNCHER%" -m streamlit run demo\demo_app.py ^
    --server.port 8502 ^
    --server.headless false ^
    --client.toolbarMode viewer ^
    --client.showErrorDetails none

if errorlevel 1 (
    echo.
    echo   Demo Mode stopped with an error. Check that Python and the
    echo   requirements are installed:  py -m pip install -r requirements.txt
    pause
)
endlocal
