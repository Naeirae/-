@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "TARGET=%~1"
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
  echo Python not found.
  pause
  exit /b 1
)

if defined TARGET (
  call %PYCMD% patch_cat.py "%TARGET%"
) else (
  call %PYCMD% patch_cat.py
)

if errorlevel 1 (
  echo.
  echo Patch failed.
  pause
  exit /b 1
)

echo.
echo Done. A new v14.2 HTML was created next to the original.
pause
