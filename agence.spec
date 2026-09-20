# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Agence Numérique Financière — Windows desktop app."""

import os
import sys as _sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Le code applicatif vit dans python/ ; on l'ajoute au chemin d'analyse pour
# que collect_submodules puisse énumérer agents/utils/models/config.
_ROOT = os.getcwd()
_PY = os.path.join(_ROOT, "python")
if _PY not in _sys.path:
    _sys.path.insert(0, _PY)

datas = []
# Magasin de certificats embarqué : sans lui, l'exe ne peut vérifier aucun
# certificat HTTPS et tous les téléchargements échouent sur
# « CERTIFICATE_VERIFY_FAILED ».
datas += collect_data_files("certifi")
try:
    datas += collect_data_files("webview")
except Exception:
    pass
# Fichier VERSION : source unique du numéro de version, lue à l'exécution par
# python/utils/version.py (/api/health, /api/diagnostic). Absent du bundle,
# l'application installée annoncerait « 0.0.0-dev ».
datas += [("VERSION", ".")]
datas += [("frontend", "frontend")]
datas += [("backend", "backend")]


def _arbre_python():
    """Arbre `python/` À EMBARQUER, sans les fichiers de la machine de build.

    Un simple ("python", "python") copiait TOUT le dossier — y compris
    `python/data/agence.db`, la base de trading créée en local dès qu'on lance
    l'application ou la suite de tests. L'installeur distribuait donc
    l'historique, les signaux et les positions de la machine qui a compilé,
    et le premier démarrage chez l'utilisateur affichait des trades qui ne
    sont pas les siens. `python/.env` (identifiants, réglages) partait de la
    même façon.
    """
    exclus_dossiers = {"__pycache__", "data", ".pytest_cache"}
    exclus_fichiers = {".env"}
    fichiers = []
    base = os.path.join(_ROOT, "python")
    for dossier, sous_dossiers, noms in os.walk(base):
        sous_dossiers[:] = [d for d in sous_dossiers if d not in exclus_dossiers]
        relatif = os.path.relpath(dossier, _ROOT)      # « python », « python/utils »…
        for nom in noms:
            if nom in exclus_fichiers or nom.endswith((".pyc", ".pyo", ".db", ".sqlite")):
                continue
            fichiers.append((os.path.join(dossier, nom), relatif))
    return fichiers


datas += _arbre_python()

# Runtimes EMBARQUÉS : Ollama (binaire + modèles), Hermès (llama-server +
# modèle GGUF) et l'installeur MetaTrader 5, préparés par
# tools/preparer_runtimes.py juste avant ce build. C'est ce qui permet de
# livrer une application « déjà installée » : au premier lancement, il n'y a
# plus rien à télécharger.
# Absent (build allégé, ou script de préparation non lancé) : l'application
# retombe sur le téléchargement à la demande, comme auparavant.
if os.path.isdir(os.path.join(_ROOT, "runtime")):
    datas += [("runtime", "runtime")]
    _mo = sum(os.path.getsize(os.path.join(_d, _f))
              for _d, _, _fs in os.walk(os.path.join(_ROOT, "runtime"))
              for _f in _fs) / 1048576
    print(f"[agence.spec] runtime/ embarque : {_mo:,.0f} Mo")
else:
    print("[agence.spec] runtime/ absent — moteurs telecharges au 1er lancement "
          "(lancez tools/preparer_runtimes.py pour les embarquer)")

# L'entrée du build est app_window.py qui lance uvicorn.run("backend.main:app")
# via une CHAÎNE : PyInstaller ne peut pas suivre cet import statiquement. On
# énumère donc explicitement l'arbre applicatif pour qu'il analyse tout le code
# réellement chargé (et embarque dotenv, scipy, etc. par transitivité).
_app_modules = []
for _pkg in ("agents", "utils", "models", "backend"):
    try:
        _app_modules += collect_submodules(_pkg)
    except Exception:
        pass

hiddenimports = _app_modules + [
    # Sources de confiance HTTPS : atteintes dynamiquement par utils/reseau
    # (import protégé), donc invisibles à l'analyse statique.
    "certifi",
    "truststore",
    "ssl",
    # Application (chargée par chaîne via uvicorn.run)
    "backend.main",
    "config",
    # Dépendances tierces chargées par les agents / config (invisibles à
    # l'analyse statique car atteintes via l'arbre applicatif ci-dessus)
    "dotenv",
    "scipy",
    "scipy.stats",
    "scipy.special",
    "scipy.special._ufuncs",
    "scipy._lib.messagestream",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "fastapi",
    "fastapi.middleware.cors",
    "fastapi.staticfiles",
    "fastapi.responses",
    "aiofiles",
    "anyio",
    "anyio._backends._asyncio",
    "anyio._backends._trio",
    "starlette",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.staticfiles",
    "email.mime.text",
    "email.mime.multipart",
    "pandas",
    "numpy",
    "yfinance",
    "webview",
    "webview.event",
    "webview.menu",
    "webview.screen",
    "webview.platforms.edgechromium",
]

# Librairie de trading réel (Windows uniquement) — présente au build sur
# Windows grâce au marqueur des requirements ; ignorée ailleurs.
import sys as _sys
if _sys.platform == "win32":
    hiddenimports.append("MetaTrader5")

# ── Identité Windows de l'exécutable ─────────────────────────────────
# Sans icône ni ressource « version », Windows présente l'application avec
# l'icône générique d'un exécutable inconnu et une fiche de propriétés vide —
# exactement l'apparence d'un binaire douteux. SmartScreen et les antivirus
# en tiennent compte, et l'utilisateur aussi.
_ICONE = os.path.join(_ROOT, "installer", "agence.ico")
if not os.path.isfile(_ICONE):
    print("[agence.spec] installer/agence.ico absent — icone par defaut "
          "(regenerez-la : python tools/generer_icone.py)")
    _ICONE = None

_VERSION_FICHIER = None
if _sys.platform == "win32":
    try:
        _sys.path.insert(0, os.path.join(_ROOT, "python"))
        from utils.version import version as _v, version_windows as _vw
        _nums = _vw().replace(".", ", ")
        _texte = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({_nums}), prodvers=({_nums}),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'Agence Numérique Financière'),
      StringStruct('FileDescription', 'Agence Numérique Financière — 46 agents IA de trading'),
      StringStruct('FileVersion', '{_v()}'),
      StringStruct('InternalName', 'AgenceNumerique'),
      StringStruct('OriginalFilename', 'AgenceNumerique.exe'),
      StringStruct('ProductName', 'Agence Numérique Financière'),
      StringStruct('ProductVersion', '{_v()}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])])
  ]
)
"""
        os.makedirs(os.path.join(_ROOT, "build"), exist_ok=True)
        _VERSION_FICHIER = os.path.join(_ROOT, "build", "version_info.txt")
        with open(_VERSION_FICHIER, "w", encoding="utf-8") as _f:
            _f.write(_texte)
        print(f"[agence.spec] version de l'exe : {_v()}")
    except Exception as _e:
        print(f"[agence.spec] ressource de version non generee : {_e}")
        _VERSION_FICHIER = None

block_cipher = None

a = Analysis(
    ["app_window.py"],
    pathex=[".", "python"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AgenceNumerique",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICONE,
    version=_VERSION_FICHIER,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    # Binaires des moteurs IA : NE PAS les compresser. UPX les décompresse en
    # mémoire à chaque lancement (plusieurs secondes pour ollama.exe) et les
    # antivirus signalent volontiers un exécutable compressé — deux façons de
    # casser un moteur qui fonctionne parfaitement décompressé.
    upx_exclude=["ollama.exe", "llama-server.exe", "mt5setup.exe",
                 "terminal64.exe"],
    name="AgenceNumerique",
)
