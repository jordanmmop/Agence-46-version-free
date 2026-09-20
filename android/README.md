# Application Android — Agence Numérique Financière

Application Android native (WebView) qui se connecte au serveur tournant
sur votre PC (`AgenceNumerique.exe` ou `python start.py`).

Au premier lancement, l'app demande l'adresse du serveur — elle est
affichée sur l'écran de démarrage de l'app bureau (carte « Sur votre
iPhone / téléphone »), par exemple `http://192.168.1.20:8765`.

## Compiler l'APK

### Option 0 — build_apk.bat (le plus simple, Windows)

Double-cliquez sur **`build_apk.bat`** à la racine du projet : il
télécharge tout seul un JDK portable + le SDK Android (première fois,
~800 Mo), compile, et dépose **`dist\AgenceNumerique.apk`** prêt à
installer. Rien d'autre à installer.

### Option 1 — Android Studio (recommandé, gratuit)

1. Installez [Android Studio](https://developer.android.com/studio)
2. **File → Open** → sélectionnez ce dossier `android/`
3. Attendez la synchronisation Gradle (première fois : quelques minutes)
4. **Build → Build App Bundle(s) / APK(s) → Build APK(s)**
5. L'APK est dans `android/app/build/outputs/apk/debug/app-debug.apk`

### Option 2 — Ligne de commande (si SDK Android installé)

```bash
cd android
./gradlew assembleDebug        # Windows : gradlew.bat assembleDebug
```

### Option 3 — GitHub Actions (cloud)

Le workflow `.github/workflows/build-apk.yml` compile l'APK
automatiquement à chaque push touchant `android/` — téléchargez
l'artefact **AgenceNumerique-Android** depuis l'onglet Actions.
(Nécessite un compte GitHub avec Actions activées : facturation
débloquée, ou dépôt public.)

## Installer sur le téléphone

1. Copiez `app-debug.apk` sur le téléphone (câble USB, Google Drive,
   ou n'importe quel partage de fichiers)
2. Ouvrez le fichier sur le téléphone → autorisez l'installation
   depuis des « sources inconnues » quand Android le demande
3. L'icône **Agence IA** apparaît dans vos applications

## Alternative sans APK — PWA Chrome

Sur Android, Chrome installe les PWA comme de vraies applications :
ouvrez l'adresse du serveur dans Chrome → menu ⋮ → **« Installer
l'application »**. Résultat identique, sans compilation.
