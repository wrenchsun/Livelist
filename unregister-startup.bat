@echo off
chcp 65001 >nul
echo ========================================
echo  Livelist スタートアップ解除
echo ========================================
echo.

schtasks /delete /tn "Livelist" /f >nul 2>&1

if errorlevel 1 (
    echo [情報] タスクは登録されていませんでした。
) else (
    echo [完了] スタートアップから削除しました。
    echo 次回ログオン時から自動起動しなくなります。
)
echo.
pause
