@echo off
rem Scripto launcher (Windows). Runs the GUI from this repo via uv;
rem `uv run` installs/syncs dependencies automatically on first launch.
setlocal
cd /d "%~dp0..\.."

where uv >nul 2>&1
if errorlevel 1 (
    echo Scripto needs uv to run. Install it with:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo Then double-click Scripto.bat again.
    pause
    exit /b 1
)

uv run scripto
if errorlevel 1 (
    echo.
    echo Scripto exited with an error. Run "uv run scripto-cli doctor" for diagnostics.
    pause
)
