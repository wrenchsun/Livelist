@echo off
chcp 65001 >nul
echo ========================================
echo  Livelist スタートアップ登録
echo ========================================
echo.

set TASK=Livelist
set BG_EXE=%~dp0LivelistBG.exe
set EXE=%~dp0Livelist.exe
set PY=%~dp0server.py

:: 実行ファイルの優先順位: LivelistBG.exe > Livelist.exe > pythonw
if exist "%BG_EXE%" (
    set RUN_CMD="%BG_EXE%"
    set RUN_MODE=LivelistBG.exe（コンソールなし）
) else if exist "%EXE%" (
    set RUN_CMD="%EXE%"
    set RUN_MODE=Livelist.exe（コンソールあり）
) else if exist "%PY%" (
    set RUN_CMD=pythonw.exe "%PY%"
    set RUN_MODE=Python（pythonw）
) else (
    echo [エラー] 実行ファイルが見つかりません。
    pause & exit /b 1
)

:: 既存タスクを削除してから再登録
schtasks /delete /tn "%TASK%" /f >nul 2>&1
schtasks /create /tn "%TASK%" /sc ONLOGON /tr %RUN_CMD% /f >nul 2>&1

if errorlevel 1 (
    echo [エラー] タスクの登録に失敗しました。
    pause & exit /b 1
)

echo [完了] スタートアップへの登録が完了しました。
echo 起動方法: %RUN_MODE%
echo 次回 Windows ログオン時から自動的に起動します。
echo.
echo 今すぐ起動しますか？ [y / それ以外で終了]
set /p ANS="> "
if /i "%ANS%"=="y" (
    start "" %RUN_CMD%
    echo.
    echo 起動しました。少し待ってからブラウザで以下にアクセスしてください。
    echo   http://localhost:8080
)
echo.
pause
