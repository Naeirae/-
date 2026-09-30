@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ==============================================================
echo   PROORGANIC public research collector
echo ==============================================================

set "PYCMD="

py -3 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"

if not defined PYCMD (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=python"
)

if not defined PYCMD (
    for /d %%D in ("%LocalAppData%\Python\pythoncore-*") do (
        if exist "%%~fD\python.exe" set "PYCMD="%%~fD\python.exe""
    )
)

if not defined PYCMD (
    for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
        if exist "%%~fD\python.exe" set "PYCMD="%%~fD\python.exe""
    )
)

if not defined PYCMD (
    echo [ERROR] Python was not found.
    echo Install Python 3.11+ or edit RUN_PROORGANIC.bat and set PYCMD manually.
    pause
    exit /b 1
)

echo [1/4] Python: %PYCMD%

if not exist ".venv\Scripts\python.exe" (
    echo [2/4] Creating virtual environment...
    call %PYCMD% -m venv ".venv"
    if errorlevel 1 goto :fail
) else (
    echo [2/4] Virtual environment already exists.
)

echo [3/4] Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

echo [4/4] Running full collection...
".venv\Scripts\python.exe" proorganic_collector.py --config config.json
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Collector exited with code %RC%.
    echo Check output\runs\...\run.log and FATAL_ERROR.txt if it exists.
    pause
    exit /b %RC%
)

echo.
echo Collection completed successfully.
if exist "output\latest\report.html" start "" "output\latest\report.html"
if exist "output\latest\PROORGANIC_public_research.xlsx" start "" "output\latest\PROORGANIC_public_research.xlsx"
exit /b 0

:fail
echo.
echo [ERROR] Environment setup failed.
pause
exit /b 1
