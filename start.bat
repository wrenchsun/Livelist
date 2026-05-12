@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 既存の Livelist プロセスを終了
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: 優先順位: LivelistBG.exe > Livelist.exe > Python
if exist "LivelistBG.exe" (
    start "" "LivelistBG.exe"
) else if exist "Livelist.exe" (
    start "" "Livelist.exe"
) else (
    python server.py
    pause
)
