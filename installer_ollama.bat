@echo off
title Installation Ollama — IA Locale (secours)
cd /d "%~dp0"
echo.
echo  ===================================================
echo   Installation de l'IA Locale (Ollama)
echo   100%% gratuit - tourne sur votre PC sans internet
echo  ===================================================
echo.
echo  NOTE : ce script n'est plus necessaire pour une application
echo  compilee avec build_exe.bat — Ollama, Hermes et MetaTrader 5
echo  y sont deja embarques. Il ne sert qu'en secours (build allege,
echo  ou installation manuelle d'Ollama sur la machine).
echo.

:: Verifier si Ollama est deja installe
ollama --version >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Ollama est deja installe.
    goto :pull_model
)

echo  [1/2] Telechargement d'Ollama...
echo  Ouverture de la page de telechargement...
start https://ollama.com/download/windows
echo.
echo  Installez Ollama, puis appuyez sur une touche pour continuer.
pause
echo.

:pull_model
echo  [2/2] Telechargement du modele IA (llama3.2 - environ 2 Go)...
echo  Cela peut prendre 5-15 minutes selon votre connexion.
echo.
ollama pull llama3.2

if errorlevel 1 (
    echo.
    echo  [ERREUR] Telechargement echoue. Verifiez qu'Ollama est bien installe.
    echo  Relancez ce script apres l'installation d'Ollama.
    pause
    exit /b 1
)

echo.
echo  ===================================================
echo   Installation terminee !
echo.
echo   Modeles disponibles :
ollama list
echo.
echo   Lancez AgenceNumerique.exe et choisissez
echo   "Local (Ollama)" dans la page de configuration.
echo  ===================================================
echo.
pause
