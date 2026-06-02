@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo === Frag standalone build ===
echo.

REM Locate a usable Python (3.11+ required for stdlib tomllib)
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "LAUNCHER=py -3"
) else (
    set "LAUNCHER=python"
)

REM Create / reuse an isolated build venv so the user's site-packages aren't dragged in
if not exist .venv-build (
    echo Creating build venv...
    %LAUNCHER% -m venv .venv-build || goto :error
)

set "PY=.venv-build\Scripts\python.exe"
if not exist "%PY%" (
    echo Build venv looks broken. Delete .venv-build and try again.
    goto :error
)

echo Installing dependencies into build venv...
"%PY%" -m pip install --upgrade pip --disable-pip-version-check --quiet || goto :error
"%PY%" -m pip install --disable-pip-version-check --quiet -r requirements.txt || goto :error
"%PY%" -m pip install --disable-pip-version-check --quiet pyinstaller || goto :error

echo Cleaning previous build artifacts...
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist
if exist Frag.spec del /q Frag.spec

REM Regenerate the icon if missing (Pillow lives in the build venv)
if not exist assets\icon.ico (
    echo Generating placeholder icon...
    "%PY%" -m pip install --disable-pip-version-check --quiet pillow || goto :error
    "%PY%" tools\make_icon.py || goto :error
)

echo.
echo Building Frag (Frag.exe, onedir)...
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name Frag ^
    --icon assets\icon.ico ^
    --paths . ^
    --add-data "assets;assets" ^
    --collect-data customtkinter ^
    --collect-submodules customtkinter ^
    --collect-submodules nbtlib ^
    launcher.py || goto :error

echo.
echo === Build complete ===
echo.
echo Launcher  : dist\Frag\Frag.exe       ^<-- user clicks this; splash + main app in one process
echo Internals : dist\Frag\_internal\
echo.
echo Ship the entire 'dist\Frag\' directory.
echo.
exit /b 0

:error
echo.
echo *** BUILD FAILED ***
echo See the messages above for the cause.
exit /b 1
