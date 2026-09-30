@echo off
cd /d "%~dp0"
if exist "output\latest\report.html" (
    start "" "output\latest\report.html"
) else (
    echo No completed run found yet.
    echo Run RUN_PROORGANIC.bat first.
    pause
)
