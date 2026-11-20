@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\pc-betrieb.ps1" -Neustart
pause
