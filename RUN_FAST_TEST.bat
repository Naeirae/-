@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run RUN_PROORGANIC.bat once to create the environment.
    pause
    exit /b 1
)

echo Running a small test: 20 site pages and 40 Telegram posts...
".venv\Scripts\python.exe" proorganic_collector.py --config config.json --max-site-pages 20 --max-site-depth 2 --max-telegram-posts 40 --no-resolve
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    if exist "output\latest\report.html" start "" "output\latest\report.html"
) else (
    echo Test failed with code %RC%.
    pause
)
exit /b %RC%
