@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ==============================================================
echo   PROORGANIC v14.2 - CAT JUMP FIX
echo ==============================================================
echo.

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
    echo Python not found.
    pause
    exit /b 1
)

%PYCMD% "%~dp0patch_cat.py"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo Fix completed.
) else (
    echo Fix failed. Send this console text to ChatGPT.
)

echo.
pause
exit /b %RC%
