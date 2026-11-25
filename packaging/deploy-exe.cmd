@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-exe.ps1" %*
exit /b %ERRORLEVEL%
