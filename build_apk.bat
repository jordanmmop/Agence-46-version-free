@echo off
cd /d "%~dp0"
title Build APK - Agence Numerique
setlocal

echo.
echo  ================================================
echo    Agence Numerique - Compilation Android (.apk)
echo  ================================================
echo.
echo  Premiere fois : telecharge JDK + SDK Android (~800 Mo)
echo  et compile. Compter 10-25 minutes. Ne fermez pas.
echo.

set ROOT=%CD%
set JDK_DIR=%ROOT%\.jdk
set SDK_DIR=%ROOT%\.android-sdk

REM ══ 1. JDK 17 portable ══════════════════════════════════════════
if exist "%JDK_DIR%\bin\java.exe" goto :jdk_ok

echo  [1/4] Telechargement du JDK 17 portable (~190 Mo)...
powershell -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk' -OutFile '%TEMP%\jdk17.zip'"
if errorlevel 1 goto :err_jdk

echo        Extraction...
powershell -Command "Expand-Archive -Force '%TEMP%\jdk17.zip' '%ROOT%\.jdk_tmp'"
if errorlevel 1 goto :err_jdk

for /d %%i in ("%ROOT%\.jdk_tmp\jdk*") do move "%%i" "%JDK_DIR%" >nul
rmdir /s /q "%ROOT%\.jdk_tmp" 2>nul
del "%TEMP%\jdk17.zip" 2>nul

if not exist "%JDK_DIR%\bin\java.exe" goto :err_jdk
:jdk_ok
echo  [1/4] JDK 17 : OK

set JAVA_HOME=%JDK_DIR%
set PATH=%JDK_DIR%\bin;%PATH%

REM ══ 2. SDK Android (cmdline-tools) ══════════════════════════════
if exist "%SDK_DIR%\cmdline-tools\latest\bin\sdkmanager.bat" goto :sdk_ok

echo  [2/4] Telechargement des outils Android (~130 Mo)...
powershell -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip' -OutFile '%TEMP%\cmdtools.zip'"
if errorlevel 1 goto :err_sdk

echo        Extraction...
powershell -Command "Expand-Archive -Force '%TEMP%\cmdtools.zip' '%SDK_DIR%\_tmp'"
if errorlevel 1 goto :err_sdk

mkdir "%SDK_DIR%\cmdline-tools" 2>nul
move "%SDK_DIR%\_tmp\cmdline-tools" "%SDK_DIR%\cmdline-tools\latest" >nul
rmdir /s /q "%SDK_DIR%\_tmp" 2>nul
del "%TEMP%\cmdtools.zip" 2>nul

if not exist "%SDK_DIR%\cmdline-tools\latest\bin\sdkmanager.bat" goto :err_sdk
:sdk_ok
echo  [2/4] Outils Android : OK

set ANDROID_HOME=%SDK_DIR%

REM ══ 3. Plateforme + build-tools + licences ══════════════════════
if exist "%SDK_DIR%\platforms\android-34" goto :plat_ok

echo  [3/4] Installation plateforme Android 34 (~500 Mo)...
echo        (acceptation automatique des licences)
(for /l %%x in (1,1,12) do @echo y) | call "%SDK_DIR%\cmdline-tools\latest\bin\sdkmanager.bat" --sdk_root="%SDK_DIR%" --licenses >nul 2>&1
call "%SDK_DIR%\cmdline-tools\latest\bin\sdkmanager.bat" --sdk_root="%SDK_DIR%" "platform-tools" "platforms;android-34" "build-tools;34.0.0"
if errorlevel 1 goto :err_plat

:plat_ok
echo  [3/4] Plateforme Android : OK

REM ══ 4. Compilation ═══════════════════════════════════════════════
echo  [4/4] Compilation de l'APK (Gradle - premiere fois ~10 min)...
echo.

set SDK_SLASH=%SDK_DIR:\=/%
echo sdk.dir=%SDK_SLASH%> "%ROOT%\android\local.properties"

cd android
call gradlew.bat assembleDebug --no-daemon
set BUILD_RC=%ERRORLEVEL%
cd ..

if not "%BUILD_RC%"=="0" goto :err_build
if not exist "android\app\build\outputs\apk\debug\app-debug.apk" goto :err_build

if not exist dist mkdir dist
copy /y "android\app\build\outputs\apk\debug\app-debug.apk" "dist\AgenceNumerique.apk" >nul

echo.
echo  ================================================
echo   [OK] SUCCES !
echo.
echo   Votre APK : dist\AgenceNumerique.apk
echo.
echo   Installation sur le telephone :
echo   1. Copiez le fichier sur le telephone
echo      (USB, Google Drive, WhatsApp a soi-meme...)
echo   2. Ouvrez-le sur le telephone
echo   3. Autorisez "sources inconnues" si demande
echo  ================================================
echo.
explorer dist
goto :fin

:err_jdk
echo.
echo  [ERREUR] Telechargement/extraction du JDK impossible.
echo  Verifiez votre connexion internet et relancez.
goto :fin

:err_sdk
echo.
echo  [ERREUR] Telechargement des outils Android impossible.
echo  Verifiez votre connexion internet et relancez.
goto :fin

:err_plat
echo.
echo  [ERREUR] Installation de la plateforme Android echouee.
echo  Relancez ce script (la reprise est automatique).
goto :fin

:err_build
echo.
echo  [ERREUR] La compilation Gradle a echoue.
echo  Faites defiler pour voir l'erreur ci-dessus, ou ouvrez le
echo  dossier android\ avec Android Studio (compilation guidee).
goto :fin

:fin
pause
