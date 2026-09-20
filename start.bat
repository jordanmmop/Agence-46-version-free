@echo off
chcp 65001 > nul
title Agence Numerique Financiere
cd /d "%~dp0"

REM Delegation vers le point d'entree REEL. Ce lanceur ouvrait auparavant une
REM interface de demonstration (positions et performances generees
REM aleatoirement) : elle a ete supprimee pour ne jamais afficher de chiffres
REM inventes dans une application qui engage de l'argent reel.
python start.py
if errorlevel 1 (
  py start.py
  if errorlevel 1 (
    echo.
    echo   [ERREUR] Python est introuvable. Installez-le depuis python.org
    echo   puis relancez ce fichier ^(ou utilisez run.bat^).
    pause
  )
)
