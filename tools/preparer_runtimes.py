#!/usr/bin/env python3
"""Prépare `runtime/` AVANT la compilation : Ollama, Hermès et MetaTrader 5.

Ce script est lancé par build_exe.bat juste avant PyInstaller. Il télécharge
une fois pour toutes les moteurs et les modèles, les range dans `runtime/`, et
PyInstaller les empaquette dans l'application. Résultat : l'utilisateur final
n'installe RIEN — il lance l'exe et tout est déjà là, y compris hors ligne.

    runtime/
      manifest.json          ce qui a été embarqué (versions, tailles, URL)
      ollama/  ollama.exe + models/     (moteur IA local n°1)
      hermes/  bin/llama-server + models/*.gguf   (moteur IA local n°2)
      mt5/     terminal/terminal64.exe  (MetaTrader 5 AvaTrade PORTABLE, prêt)
               mt5setup.exe             (installeur AvaTrade, repli)

Le terminal embarqué doit être celui du COURTIER : un MetaTrader générique
démarre très bien mais ne connaît aucun serveur AvaTrade, donc ne peut pas se
connecter au compte. Le script refuse d'en embarquer un (--terminal-mt5-generique
pour passer outre) et retire le dossier si le contrôle final échoue.

Usage :

    python tools/preparer_runtimes.py                  # tout
    python tools/preparer_runtimes.py --sans-hermes    # sans le 2e moteur
    python tools/preparer_runtimes.py --modele-ollama llama3.2
    python tools/preparer_runtimes.py --modele-hermes hermes-3-llama-3.1-8b
    python tools/preparer_runtimes.py --sans-terminal-mt5  # installeur seul
    MT5_SETUP_URL=file:///C:/chemin/avatrade5setup.exe python tools/preparer_runtimes.py
    python tools/preparer_runtimes.py --verifier       # état, sans rien faire

Le script est IDEMPOTENT : ce qui est déjà présent et complet n'est pas
retéléchargé. Un composant en échec n'arrête pas les autres — il est signalé
en fin d'exécution, et l'application saura le télécharger à la demande au
premier lancement (ce qui reste le comportement historique).

Attention aux volumes : le dossier final pèse 5 à 10 Go selon les modèles
choisis, et la compilation dure d'autant plus longtemps.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

RACINE = Path(__file__).resolve().parent.parent
RUNTIME = RACINE / "runtime"
PYTHON_DIR = RACINE / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


# ── Sortie console ────────────────────────────────────────────────────
def info(msg):  print(f"  {msg}", flush=True)
def ok(msg):    print(f"  [OK] {msg}", flush=True)
def warn(msg):  print(f"  [!] {msg}", flush=True)
def titre(msg): print(f"\n=== {msg} ===", flush=True)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def telecharger(url: str, cible: Path, libelle: str) -> Path:
    """Téléchargement avec barre de progression et contrôle de complétude.

    Le fichier n'est mis en place qu'une fois COMPLET (écriture dans un .part
    puis renommage) : une coupure ne laisse jamais un binaire ou un modèle
    tronqué que le build empaquetterait tel quel.
    """
    cible.parent.mkdir(parents=True, exist_ok=True)
    part = cible.with_suffix(cible.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "AgenceNumerique/build"})
    debut = time.time()
    # Magasins de certificats en cascade (antivirus inspectant le HTTPS,
    # magasin Windows incomplet) : sans cela le build échoue sur
    # « CERTIFICATE_VERIFY_FAILED » et l'exe part sans ses moteurs.
    from utils import reseau
    with reseau.ouvrir(req, timeout=120) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        lu = 0
        dernier = 0.0
        while True:
            bloc = resp.read(1024 * 512)
            if not bloc:
                break
            f.write(bloc)
            lu += len(bloc)
            if total and time.time() - dernier > 0.5:
                dernier = time.time()
                pct = lu / total * 100
                mo = lu / 1_048_576
                print(f"\r  {libelle} : {pct:5.1f} %  ({mo:,.0f} Mo)",
                      end="", flush=True)
    print(f"\r  {libelle} : terminé ({lu / 1_048_576:,.0f} Mo "
          f"en {time.time() - debut:.0f} s)" + " " * 12, flush=True)
    if total and lu < total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"téléchargement incomplet ({lu}/{total} octets)")
    part.replace(cible)
    return cible


# ═══════════════════════════ OLLAMA ═══════════════════════════════════
def preparer_ollama(modele: str, manifeste: dict):
    """Binaire Ollama + modèle déjà tiré, prêts à être empaquetés."""
    titre(f"Ollama (moteur IA local n°1) — modèle {modele}")
    from utils.ollama_embedded import urls_pour_plateforme

    dossier = RUNTIME / "ollama"
    exe = dossier / ("ollama.exe" if platform.system() == "Windows" else "bin/ollama")

    if not exe.is_file():
        urls = urls_pour_plateforme()
        if not urls:
            raise RuntimeError(f"Ollama indisponible pour {platform.system()}")
        archive = dossier / ("ollama.zip" if urls[0].endswith(".zip") else "ollama.tgz")
        derniere = None
        for url in urls:
            try:
                telecharger(url, archive, "Ollama")
                manifeste.setdefault("sources", {})["ollama"] = url
                break
            except Exception as e:
                derniere = e
                archive.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"téléchargement d'Ollama impossible : {derniere}")

        info("Extraction...")
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(dossier)
        else:
            with tarfile.open(archive) as t:
                try:
                    t.extractall(dossier, filter="data")
                except TypeError:            # Python < 3.12
                    t.extractall(dossier)
        archive.unlink(missing_ok=True)
        for cand in (dossier / "ollama.exe", dossier / "bin" / "ollama", dossier / "ollama"):
            if cand.is_file():
                if platform.system() != "Windows":
                    cand.chmod(0o755)
                exe = cand
                break
        else:
            raise RuntimeError("binaire ollama introuvable après extraction")
    ok(f"Binaire : {exe}")

    # ── Modèle : tiré AVEC le binaire qu'on vient d'embarquer, dans le
    # dossier qu'il utilisera à l'exécution. Le serveur est démarré le temps
    # du `pull` puis arrêté : rien ne reste en fond après le build.
    modeles = dossier / "models"
    if _modele_ollama_present(modeles, modele):
        ok(f"Modèle {modele} déjà embarqué")
    else:
        info(f"Téléchargement du modèle {modele} (plusieurs Go)...")
        _tirer_modele_ollama(exe, modeles, modele)
        ok(f"Modèle {modele} embarqué")

    manifeste["ollama"] = {
        "binaire": str(exe.relative_to(RUNTIME)),
        "modele": modele,
        "taille_mo": round(_taille(dossier) / 1_048_576),
    }


def _modele_ollama_present(modeles: Path, modele: str) -> bool:
    """Le manifeste du modèle est-il déjà dans le dossier embarqué ?

    On regarde les fichiers de manifeste d'Ollama plutôt que la sortie de
    `ollama list` : le serveur n'est pas forcément démarré à ce moment-là.
    """
    racine = modeles / "manifests"
    if not racine.is_dir():
        return False
    nom, _, tag = modele.partition(":")
    tag = tag or "latest"
    for chemin in racine.rglob(tag):
        if chemin.is_file() and chemin.parent.name == nom:
            return True
    return False


def _tirer_modele_ollama(exe: Path, modeles: Path, modele: str):
    modeles.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OLLAMA_MODELS"] = str(modeles)
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    serveur = subprocess.Popen([str(exe), "serve"], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):                  # attendre l'ouverture du port
            try:
                with _opener().open("http://127.0.0.1:11434/api/tags", timeout=2):
                    break
            except Exception:
                if serveur.poll() is not None:
                    raise RuntimeError("le serveur Ollama s'est arrêté au démarrage")
                time.sleep(1)
        else:
            raise RuntimeError("le serveur Ollama n'a pas démarré (60 s)")

        resultat = subprocess.run([str(exe), "pull", modele], env=env)
        if resultat.returncode != 0:
            raise RuntimeError(f"« ollama pull {modele} » a échoué "
                               f"(code {resultat.returncode})")
    finally:
        serveur.terminate()
        try:
            serveur.wait(timeout=15)
        except subprocess.TimeoutExpired:
            serveur.kill()


# ═══════════════════════════ HERMÈS ═══════════════════════════════════
def preparer_hermes(modele: str, manifeste: dict):
    """Serveur llama.cpp + modèle GGUF Hermès."""
    titre(f"Hermès (moteur IA local n°2) — modèle {modele}")
    from utils.hermes_embedded import (MODELES, nom_fichier_modele,
                                       url_modele, urls_llama_server)

    if modele not in MODELES:
        raise RuntimeError(f"modèle Hermès inconnu : {modele} "
                           f"(connus : {', '.join(MODELES)})")

    dossier = RUNTIME / "hermes"
    binaires = dossier / "bin"
    nom_exe = "llama-server.exe" if platform.system() == "Windows" else "llama-server"

    if not (binaires / nom_exe).is_file():
        urls = urls_llama_server()
        if not urls:
            raise RuntimeError(
                f"aucun binaire llama.cpp pour {platform.system()} — "
                "indiquez-en un avec LLAMA_SERVER_URL")
        archive = dossier / "llama.zip"
        derniere = None
        for url in urls:
            try:
                telecharger(url, archive, "Moteur Hermès (llama.cpp)")
                manifeste.setdefault("sources", {})["hermes_serveur"] = url
                break
            except Exception as e:
                derniere = e
                archive.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"téléchargement du moteur impossible : {derniere}")

        info("Extraction...")
        binaires.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            membres = [m for m in z.namelist() if not m.endswith("/")]
            # PurePosixPath : un ZIP nomme ses membres avec des « / », que
            # Path() traduirait en « \ » sous Windows — le préfixe ne
            # correspondrait alors à aucun membre et l'archive serait
            # « extraite » sans copier un seul fichier.
            porteurs = [m for m in membres if PurePosixPath(m).name == nom_exe]
            if not porteurs:
                raise RuntimeError(f"{nom_exe} absent de l'archive llama.cpp")
            prefixe = str(PurePosixPath(porteurs[0]).parent)
            for membre in membres:
                if prefixe not in (".", "") and not membre.startswith(prefixe):
                    continue
                # Aplati : le serveur charge ses bibliothèques depuis son
                # propre dossier, quelle que soit l'arborescence de l'archive.
                cible_membre = binaires / PurePosixPath(membre).name
                with z.open(membre) as src, open(cible_membre, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        archive.unlink(missing_ok=True)
        if platform.system() != "Windows":
            for p in binaires.iterdir():
                if p.is_file():
                    p.chmod(0o755)
    ok(f"Serveur : {binaires / nom_exe}")

    cible = dossier / "models" / nom_fichier_modele(modele)
    if cible.is_file():
        ok(f"Modèle {modele} déjà embarqué ({cible.stat().st_size / 1_048_576:,.0f} Mo)")
    else:
        url = url_modele(modele)
        telecharger(url, cible, f"Modèle Hermès {modele}")
        manifeste.setdefault("sources", {})["hermes_modele"] = url
        ok(f"Modèle {modele} embarqué")

    manifeste["hermes"] = {
        "serveur": str((binaires / nom_exe).relative_to(RUNTIME)),
        "modele": modele,
        "fichier": cible.name,
        "taille_mo": round(_taille(dossier) / 1_048_576),
    }


# ═══════════════════════ METATRADER 5 ═════════════════════════════════
# Installeur du COURTIER. C'est lui, et lui seul, qui apporte les serveurs
# AvaTrade (fichiers .srv) : un terminal MetaTrader générique démarre très
# bien, mais ne sait joindre AUCUN serveur AvaTrade et refuse donc toute
# connexion au compte, sans que rien n'explique pourquoi.
# MT5_SETUP_URL permet de pointer l'installeur téléchargé depuis avatrade.fr
# (ou un miroir interne) quand le CDN MetaQuotes est inaccessible.
_URLS_MT5_AVATRADE = [
    os.getenv("MT5_SETUP_URL", ""),
    "https://download.mql5.com/cdn/web/ava.trade.markets/mt5/avatrade5setup.exe",
]

# MetaTrader GÉNÉRIQUE. Repli pour l'installeur embarqué UNIQUEMENT : il
# permet au moins d'installer MetaTrader au premier démarrage. Il n'est
# JAMAIS utilisé pour produire le terminal portable sans demande explicite
# (--terminal-mt5-generique), car il ne connaît pas AvaTrade.
_URLS_MT5_GENERIQUE = [
    "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe",
]


def _installeur_est_avatrade(setup: Path) -> bool:
    """L'installeur porte-t-il la marque du courtier ?

    Les installeurs MetaQuotes sont personnalisés par courtier : le nom de
    l'entité apparaît en clair dans le binaire. Cela permet de reconnaître un
    installeur AvaTrade téléchargé À LA MAIN et déposé dans runtime/mt5/,
    au lieu de le confondre avec le générique.
    """
    try:
        with open(setup, "rb") as f:
            debut = f.read(8 * 1024 * 1024).lower()
        return b"ava" in debut and (b"avatrade" in debut or b"ava.trade" in debut)
    except Exception:
        return False


def preparer_mt5(manifeste: dict, portable: bool = True,
                 exiger_avatrade: bool = True):
    """MetaTrader 5 embarqué — de préférence un terminal PORTABLE prêt à l'emploi.

    Deux niveaux, du meilleur au moins bon :

    1. **Terminal portable** (Windows uniquement) : l'installeur officiel est
       exécuté ICI, au build, et le terminal obtenu est recopié dans
       `runtime/mt5/terminal/`. L'application le lance ensuite en mode
       « /portable » — MetaTrader est donc RÉELLEMENT déjà installé pour
       l'utilisateur final, qui n'a plus qu'à saisir ses identifiants.
    2. **Installeur embarqué** (repli, et seul choix hors Windows) : le
       programme d'installation est empaqueté tel quel, et l'application le
       lance une fois au premier démarrage.

    MetaQuotes ne publie pas d'archive portable : produire (1) impose donc de
    compiler sur Windows. Sur un autre système, on s'arrête à (2).
    """
    titre("MetaTrader 5 AvaTrade (exécution des ordres)")
    cible = RUNTIME / "mt5" / "mt5setup.exe"
    if cible.is_file():
        marque = "AvaTrade" if _installeur_est_avatrade(cible) else "générique"
        ok(f"Installeur déjà embarqué — {marque} "
           f"({cible.stat().st_size / 1_048_576:,.0f} Mo)")
    else:
        derniere = None
        # L'installeur AvaTrade EN PREMIER : c'est le seul qui apporte les
        # serveurs du courtier. Le générique n'est qu'un dernier recours pour
        # que l'application puisse au moins installer MetaTrader.
        for url in [u for u in _URLS_MT5_AVATRADE + _URLS_MT5_GENERIQUE if u]:
            try:
                telecharger(url, cible, "Installeur MetaTrader 5")
                if cible.stat().st_size < 500_000:
                    raise RuntimeError("fichier trop petit — page d'erreur ?")
                manifeste.setdefault("sources", {})["mt5"] = url
                break
            except Exception as e:
                derniere = e
                warn(f"{url} : {e}")
                cible.unlink(missing_ok=True)
        else:
            raise RuntimeError(
                f"téléchargement de MetaTrader 5 impossible : {derniere}. "
                f"Téléchargez l'installeur AvaTrade depuis avatrade.fr, puis "
                f"relancez avec MT5_SETUP_URL=file:///chemin/vers/avatrade5setup.exe")
        if _installeur_est_avatrade(cible):
            ok("Installeur AvaTrade embarqué")
        else:
            warn("Installeur GÉNÉRIQUE embarqué (celui d'AvaTrade était "
                 "inaccessible) — il ne connaîtra pas les serveurs du courtier.")

    dossier_portable = RUNTIME / "mt5" / "terminal"
    if (dossier_portable / "terminal64.exe").is_file():
        ok(f"Terminal PORTABLE déjà embarqué "
           f"({_taille(dossier_portable) / 1_048_576:,.0f} Mo) — "
           f"aucune installation ne sera nécessaire")
    elif not portable:
        info("Terminal portable non demandé (--sans-terminal-mt5)")
    elif platform.system() != "Windows":
        info("Terminal portable non produit : il faut compiler sur Windows. "
             "L'installeur embarqué sera lancé au premier démarrage.")
    else:
        _produire_terminal_portable(cible, dossier_portable,
                                    exiger_avatrade=exiger_avatrade)

    serveurs = _serveurs_du_terminal(dossier_portable)
    manifeste["mt5"] = {
        "installeur": "mt5/mt5setup.exe",
        "installeur_avatrade": _installeur_est_avatrade(cible),
        "portable": (dossier_portable / "terminal64.exe").is_file(),
        # Serveurs que le terminal embarqué connaît : s'ils manquent, la
        # connexion au compte échouera quoi que fasse l'utilisateur.
        "serveurs": serveurs,
        "courtier": "avatrade" if any("ava" in s.lower() for s in serveurs)
                    else ("metaquotes" if serveurs else ""),
        "taille_mo": round(_taille(RUNTIME / "mt5") / 1_048_576),
    }
    if manifeste["mt5"]["portable"]:
        ok(f"Terminal AvaTrade embarqué — serveurs : {', '.join(serveurs[:5])}")


def _serveurs_du_terminal(dossier: Path) -> list:
    """Serveurs connus d'une installation MetaTrader (fichiers .srv)."""
    noms = []
    for srv in dossier.rglob("*.srv"):
        nom = srv.stem
        if nom and nom not in noms:
            noms.append(nom)
    return sorted(noms)


def _produire_terminal_portable(setup: Path, destination: Path,
                                exiger_avatrade: bool = True):
    """Installe MetaTrader 5 sur la machine de build, puis en copie le terminal.

    C'est la seule façon d'obtenir un MetaTrader portable : MetaQuotes ne
    distribue qu'un installeur. Le dossier copié fonctionne ensuite en mode
    « /portable » depuis n'importe où — c'est ce que fait l'application.

    Le terminal doit être celui du COURTIER. Un MetaTrader générique démarre
    parfaitement, mais ne connaît aucun serveur AvaTrade : il refuse alors
    toute connexion au compte, et l'utilisateur ne voit qu'un échec sans
    cause. On vérifie donc, APRÈS la copie, que les serveurs du courtier sont
    bien là — et on retire le dossier s'ils manquent, plutôt que de livrer un
    terminal qui ne peut pas fonctionner.

    Un échec n'est PAS fatal : l'installeur reste embarqué, et l'application
    retombe sur l'installation au premier démarrage.
    """
    if exiger_avatrade and not _installeur_est_avatrade(setup):
        warn("L'installeur embarqué n'est pas celui d'AvaTrade : aucun terminal "
             "portable ne sera produit (il ne connaîtrait pas les serveurs du "
             "courtier). Téléchargez l'installeur depuis avatrade.fr et "
             "relancez avec MT5_SETUP_URL=file:///chemin/vers/avatrade5setup.exe, "
             "ou acceptez un terminal générique avec --terminal-mt5-generique.")
        return

    info("Installation de MetaTrader 5 sur cette machine (pour en extraire "
         "le terminal)...")
    try:
        avant = set(_terminaux_installes())
        proc = subprocess.Popen([str(setup), "/auto"])
        fin_attente = time.time() + 300
        trouve = ""
        while time.time() < fin_attente:
            nouveaux = [t for t in _terminaux_installes() if t not in avant]
            if nouveaux:
                trouve = _choisir_terminal(nouveaux, exiger_avatrade)
                break
            if proc.poll() is not None and not nouveaux:
                # L'installeur a rendu la main : peut-être une installation
                # DÉJÀ présente, qu'on peut copier telle quelle.
                existants = _terminaux_installes()
                if existants:
                    trouve = _choisir_terminal(existants, exiger_avatrade)
                break
            time.sleep(3)
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        if not trouve:
            warn("Aucun terminal AvaTrade trouvé après installation — "
                 "l'installeur reste embarqué et sera lancé au premier "
                 "démarrage.")
            return

        source = Path(trouve).parent
        info(f"Copie du terminal depuis {source}...")
        destination.mkdir(parents=True, exist_ok=True)
        # `logs` et `Tester` sont des données d'exécution de la machine de
        # build : les embarquer gonflerait l'application sans rien apporter.
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("logs", "Tester", "*.log"))
        if not (destination / "terminal64.exe").is_file():
            warn("Copie incomplète — terminal64.exe absent de la destination")
            shutil.rmtree(destination, ignore_errors=True)
            return

        # ── Contrôle FINAL : le terminal connaît-il vraiment AvaTrade ? ──
        # C'est ce qui décide si l'utilisateur pourra se connecter. Un
        # terminal sans les serveurs du courtier est pire qu'aucun terminal :
        # il démarre, paraît fonctionner, et échoue au moment du compte.
        serveurs = _serveurs_du_terminal(destination)
        if exiger_avatrade and not any("ava" in s.lower() for s in serveurs):
            warn(f"Le terminal copié ne connaît AUCUN serveur AvaTrade "
                 f"({', '.join(serveurs[:5]) or 'aucun serveur'}) : il est "
                 f"RETIRÉ de l'application plutôt que livré inutilisable. "
                 f"Ouvrez MetaTrader 5 une fois sur cette machine "
                 f"(Fichier → Ouvrir un compte → « AvaTrade ») pour qu'il "
                 f"télécharge les serveurs, puis relancez ce script.")
            shutil.rmtree(destination, ignore_errors=True)
            return

        ok(f"Terminal PORTABLE embarqué "
           f"({_taille(destination) / 1_048_576:,.0f} Mo) — serveurs : "
           f"{', '.join(serveurs[:5]) or 'aucun'}")
    except Exception as e:
        warn(f"Terminal portable non produit ({e}) — l'installeur embarqué "
             f"prendra le relais au premier démarrage")


def _choisir_terminal(candidats: list, exiger_avatrade: bool = True) -> str:
    """Terminal à embarquer — celui du COURTIER, ou rien.

    Un terminal MetaTrader générique (MetaQuotes) ne connaît AUCUN serveur
    AvaTrade : livré tel quel, il démarre puis refuse toute connexion au
    compte, et le journal n'affiche qu'un « IPC timeout » sans rapport avec la
    vraie cause. Prendre le premier de la liste suffisait à embarquer le
    mauvais dès que la machine de build avait déjà un MetaTrader installé.

    On reconnaît le terminal du courtier à son CHEMIN (« AvaTrade MT5
    Terminal ») ou à ses serveurs .srv — le chemin d'une installation
    personnalisée ne dit pas toujours le courtier.
    """
    def _est_avatrade(chemin: str) -> bool:
        if "ava" in chemin.lower():
            return True
        return any("ava" in s.lower()
                   for s in _serveurs_du_terminal(Path(chemin).parent))

    prefere = [c for c in candidats if _est_avatrade(c)]
    if prefere:
        ok(f"Terminal du courtier retenu : {prefere[0]}")
        return prefere[0]
    if exiger_avatrade:
        warn(f"Aucun terminal AvaTrade parmi : {', '.join(candidats) or 'aucun'}. "
             f"Rien ne sera embarqué — un terminal générique ne pourrait pas "
             f"se connecter à AvaTrade. Utilisez --terminal-mt5-generique pour "
             f"l'accepter quand même.")
        return ""
    warn(f"Terminal générique retenu ({candidats[0]}) — il ne connaîtra pas "
         f"les serveurs AvaTrade.")
    return candidats[0]


def _terminaux_installes() -> list:
    """Chemins des terminal64.exe présents sur la machine de build."""
    import glob as _glob
    chemins = [
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files\AvaTrade MT5 Terminal\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
        r"C:\Program Files (x86)\AvaTrade MT5 Terminal\terminal64.exe",
    ]
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        chemins += _glob.glob(os.path.join(appdata, "MetaQuotes", "Terminal",
                                           "*", "terminal64.exe"))
    return [c for c in chemins if os.path.isfile(c)]


# ═══════════════════════════ Utilitaires ══════════════════════════════
def _taille(dossier: Path) -> int:
    total = 0
    if not dossier.exists():
        return 0
    for p in dossier.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def etat():
    """Affiche ce qui est déjà présent, sans rien télécharger."""
    titre("État de runtime/")
    if not RUNTIME.is_dir():
        warn(f"{RUNTIME} n'existe pas — rien n'est embarqué")
        return
    from utils import runtime_embarque
    os.environ["AGENCE_RUNTIME_DIR"] = str(RUNTIME)
    for cle, valeur in runtime_embarque.resume().items():
        if cle == "manifeste":
            continue
        info(f"{cle:16} : {valeur}")
    info(f"{'taille totale':16} : {_taille(RUNTIME) / 1_048_576:,.0f} Mo")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--modele-ollama", default=os.getenv("OLLAMA_MODEL", "llama3.2"))
    p.add_argument("--modele-hermes", default=os.getenv("HERMES_MODEL",
                                                        "hermes-3-llama-3.2-3b"))
    p.add_argument("--sans-ollama", action="store_true")
    p.add_argument("--sans-hermes", action="store_true")
    p.add_argument("--sans-mt5", action="store_true")
    p.add_argument("--sans-terminal-mt5", action="store_true",
                   help="embarquer seulement l'installeur MetaTrader, pas un "
                        "terminal portable prêt à l'emploi")
    p.add_argument("--terminal-mt5-generique", action="store_true",
                   help="accepter un terminal MetaTrader GÉNÉRIQUE si celui "
                        "d'AvaTrade est introuvable. Par défaut le script "
                        "refuse : un terminal générique ne connaît aucun "
                        "serveur AvaTrade et ne peut pas se connecter au "
                        "compte.")
    p.add_argument("--verifier", action="store_true",
                   help="affiche l'état de runtime/ sans rien télécharger")
    args = p.parse_args()

    if args.verifier:
        etat()
        return 0

    print("\n" + "=" * 62)
    print("  Préparation des runtimes embarqués")
    print("  (téléchargement unique — plusieurs Go, prévoyez du temps)")
    print("=" * 62)

    RUNTIME.mkdir(parents=True, exist_ok=True)
    # Le manifeste précédent est REPRIS : un second build ne retéléchargeant
    # rien, repartir d'un dictionnaire vide effaçait les URL sources déjà
    # enregistrées — la traçabilité de ce qui est réellement dans l'exe.
    manifeste = {}
    ancien = RUNTIME / "manifest.json"
    if ancien.is_file():
        try:
            charge = json.loads(ancien.read_text(encoding="utf-8"))
            if isinstance(charge, dict):
                manifeste = charge
        except Exception:
            pass
    manifeste["genere_le"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifeste["plateforme"] = f"{platform.system()}/{platform.machine()}"

    echecs = []
    taches = []
    if not args.sans_ollama:
        taches.append(("Ollama", lambda: preparer_ollama(args.modele_ollama, manifeste)))
    if not args.sans_hermes:
        taches.append(("Hermès", lambda: preparer_hermes(args.modele_hermes, manifeste)))
    if not args.sans_mt5:
        taches.append(("MetaTrader 5",
                       lambda: preparer_mt5(
                           manifeste,
                           portable=not args.sans_terminal_mt5,
                           exiger_avatrade=not args.terminal_mt5_generique)))

    for nom, tache in taches:
        try:
            tache()
        except Exception as e:
            # Un composant en échec ne doit pas faire échouer le build : sans
            # lui, l'application retombe simplement sur le téléchargement à la
            # demande — dégradé, mais fonctionnel.
            echecs.append((nom, e))
            warn(f"{nom} : {e}")

    (RUNTIME / "manifest.json").write_text(
        json.dumps(manifeste, indent=2, ensure_ascii=False), encoding="utf-8")

    titre("Bilan")
    info(f"Dossier : {RUNTIME}")
    info(f"Taille totale : {_taille(RUNTIME) / 1_048_576:,.0f} Mo")
    for nom, e in echecs:
        warn(f"{nom} NON embarqué ({e}) — l'application le téléchargera "
             f"au premier lancement")

    # Le bilan décrit ce qui est RÉELLEMENT dans runtime/, et non le simple
    # fait qu'aucune tâche n'a levé d'exception. Il annonçait « tout est
    # embarqué » après un build volontairement partiel (--sans-ollama) ou
    # quand le terminal MetaTrader portable n'avait pas pu être produit :
    # le compilateur pouvait livrer un exe incomplet en croyant l'inverse.
    os.environ["AGENCE_RUNTIME_DIR"] = str(RUNTIME)
    from utils import runtime_embarque
    # PAS « etat » : ce nom est déjà celui de la fonction du module (--verifier).
    # L'y écraser en fait une variable locale sur TOUTE la fonction main(), et
    # l'appel `etat()` du début lève alors UnboundLocalError.
    contenu = runtime_embarque.resume()
    courtier = (manifeste.get("mt5") or {}).get("courtier", "")
    attendu = [
        ("Moteur IA Ollama",          contenu["ollama"] and contenu["ollama_modeles"],
         args.sans_ollama),
        ("Moteur IA Hermès",          contenu["hermes"] and contenu["hermes_modele"],
         args.sans_hermes),
        # Un terminal qui ne connaît pas AvaTrade ne compte PAS comme présent :
        # il démarrerait sans jamais pouvoir se connecter au compte.
        ("Terminal MetaTrader AvaTrade",
         contenu["mt5_portable"] and courtier == "avatrade",
         args.sans_mt5 or args.sans_terminal_mt5),
        ("MetaTrader 5 (installeur)", contenu["mt5"], args.sans_mt5),
    ]
    print()
    manquants = []
    for libelle, present, ignore in attendu:
        if present:
            ok(f"{libelle} : embarqué")
        elif ignore:
            info(f"{libelle} : volontairement absent")
        else:
            warn(f"{libelle} : ABSENT")
            manquants.append(libelle)

    print()
    if manquants:
        warn("L'exe compilé sera INCOMPLET : " + ", ".join(manquants) + ". "
             "L'application téléchargera ou installera ce qui manque au "
             "premier lancement.")
    elif contenu["mt5_portable"] and courtier == "avatrade":
        ok("Tout est embarqué — l'utilisateur final n'aura RIEN à installer, "
           "et le terminal connaît déjà les serveurs AvaTrade")
    else:
        ok("Moteurs embarqués. MetaTrader sera installé au premier "
           "démarrage (terminal portable non inclus).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
