@echo off
REM Raccourci a double-cliquer : genere l'application ET son installeur Windows.
REM Toute la logique vit dans scripts\build_installer.bat.
cd /d "%~dp0"
call scripts\build_installer.bat %*
echo.
pause
