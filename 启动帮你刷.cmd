@echo off
rem Windows launcher: prepares the environment, then starts the command line version.
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %EXITCODE%
