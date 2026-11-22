@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build_setup.ps1"
set "build_status=%errorlevel%"
if not "%build_status%"=="0" pause
exit /b %build_status%
