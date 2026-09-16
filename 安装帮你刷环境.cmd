@echo off
rem Windows installer: Python 3.11+, virtual environment and dependencies.
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
