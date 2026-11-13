@echo off
cd /d "%~dp0"
python -m hornet.launcher %*
if errorlevel 1 pause
