@echo off
rem Runs ai-coder.ps1 from the current folder (OpenCode works on the folder you run this from).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ai-coder.ps1" %*
exit /b %errorlevel%
