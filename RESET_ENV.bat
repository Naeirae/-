@echo off
setlocal
cd /d "%~dp0"
echo This deletes only the local Python virtual environment .venv.
echo Collected output will NOT be deleted.
choice /C YN /M "Delete .venv"
if errorlevel 2 exit /b 0
if exist ".venv" rmdir /S /Q ".venv"
echo Done. Run RUN_PROORGANIC.bat to recreate it.
pause
