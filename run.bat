@echo off
setlocal enabledelayedexpansion
title Agence Numerique Financiere

echo.
echo   ============================================
echo     Agence Numerique Financiere - Lancement
echo   ============================================
echo.

REM ── Verifier si Python est installe ─────────────────────────────
REM NOTE : "if errorlevel 1" est evalue a l'execution, contrairement a
REM %errorlevel% qui est fige au parsing dans un bloc parenthese.
python --version >nul 2>&1
if errorlevel 1 (
  py --version >nul 2>&1
  if errorlevel 1 (
    echo   [!] Python n'est pas installe sur ce PC.
    echo.
    echo   Installation automatique de Python en cours...
    echo   ^(Telechargement depuis python.org^)
    echo.
    REM Telecharger Python avec PowerShell
    powershell -Command "& {Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\python_installer.exe'}"
    if errorlevel 1 (
      echo   [ERREUR] Telechargement echoue. Installez Python manuellement :
      echo   https://www.python.org/downloads/
      echo.
      echo   Puis relancez ce fichier.
      pause
      exit /b 1
    )
    echo   Installation de Python...
    "%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
    if errorlevel 1 (
      echo   [ERREUR] Installation echouee. Installez Python manuellement :
      echo   https://www.python.org/downloads/
      pause
      exit /b 1
    )
    echo   [OK] Python installe !
    echo.
    REM Ne PAS se relancer via start "" "%~f0" : le nouveau cmd herite d'un
    REM PATH fige (sans Python) et retelechargerait Python en boucle. On appelle
    REM directement le python fraichement installe par son chemin connu.
    set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if exist "!PYEXE!" (
      echo   Lancement de l'application...
      "!PYEXE!" start.py
      if errorlevel 1 pause
    ) else (
      echo   Python est installe. Fermez cette fenetre puis rouvrez run.bat
      echo   pour lancer l'application.
      pause
    )
    exit /b 0
  ) else (
    set PYTHON=py
  )
) else (
  set PYTHON=python
)

echo   Python trouve. Lancement de l'application...
echo.
%PYTHON% start.py
if errorlevel 1 (
  echo.
  echo   [ERREUR] Le lancement a echoue.
  echo   Verifiez que Python 3.10+ est installe : https://www.python.org/downloads/
  pause
)
