@echo off
chcp 65001 >nul
echo ========================================
echo  Livelist ビルド
echo ========================================
echo.

:: 使用する Python を決定（3.12 優先）
set PY=
where python3.12 >nul 2>&1 && set PY=python3.12
if not defined PY where python3.11 >nul 2>&1 && set PY=python3.11
if not defined PY set PY=python

:: PyInstaller の場所を探す
set PI=
for /f "delims=" %%p in ('%PY% -c "import site,os; d=site.getusersitepackages(); p=os.path.join(os.path.dirname(d),'Scripts','pyinstaller.exe'); print(p)" 2^>nul') do (
    if exist "%%p" set PI=%%p
)
if not defined PI (
    for /f "delims=" %%p in ('%PY% -c "import sys,os; print(os.path.join(os.path.dirname(sys.executable),'Scripts','pyinstaller.exe'))" 2^>nul') do (
        if exist "%%p" set PI=%%p
    )
)
if not defined PI (
    echo PyInstaller が見つかりません。インストール中...
    %PY% -m pip install pyinstaller
    if errorlevel 1 (
        echo [エラー] インストールに失敗しました。
        pause & exit /b 1
    )
    for /f "delims=" %%p in ('%PY% -c "import site,os; d=site.getusersitepackages(); p=os.path.join(os.path.dirname(d),'Scripts','pyinstaller.exe'); print(p)" 2^>nul') do (
        if exist "%%p" set PI=%%p
    )
)
if not defined PI (
    echo [エラー] PyInstaller を起動できませんでした。
    pause & exit /b 1
)

:: git情報取得（git環境外では unknown / local を使用）
set BRANCH=unknown
set COMMIT=local
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set BRANCH=%%b
for /f "delims=" %%c in ('git rev-parse --short HEAD 2^>nul') do set COMMIT=%%c
echo ビルド情報: v1.1.0 / %BRANCH% / %COMMIT%

:: _build_info.py を生成（ビルド情報を exe に埋め込む）
%PY% -c "f=open('_build_info.py','w',encoding='utf-8'); f.write(\"__version__ = '1.1.0'\n__branch__  = '%BRANCH%'\n__commit__  = '%COMMIT%'\n\")"

:: source/ から実行し、exeをルート（..）に直接出力
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo 使用 Python: %%v
echo ビルド中...
"%PI%" livelist.spec --noconfirm --distpath .. --workpath build

if errorlevel 1 (
    echo.
    echo [エラー] ビルドに失敗しました。
    pause & exit /b 1
)

echo.
echo ========================================
echo  完了
echo ========================================
echo   Livelist.exe   （コンソールあり）
echo   LivelistBG.exe （バックグラウンド）
echo   → どちらも上のフォルダに出力されました
echo.
pause
