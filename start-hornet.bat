@echo off
cd /d "%~dp0"
py -3 -m hornet.desktop %*
if errorlevel 1 pause
