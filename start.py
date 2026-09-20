#!/usr/bin/env python3
"""
start.py — Lanceur universel de l'Agence Numérique Financière
─────────────────────────────────────────────────────────────
Windows  : double-cliquez sur ce fichier
Mac/Linux: python start.py
"""
import os
import sys
import subprocess
import time
import socket
import shutil
import webbrowser
from pathlib import Path

ROOT  = Path(__file__).parent.resolve()
VENV  = ROOT / ".venv"
PY    = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
PORT  = 8000
URL   = f"http://localhost:{PORT}"

# ── Couleurs (désactivées sur Windows sans support ANSI) ──────────────
NO_COLOR = sys.platform == "win32" and not os.environ.get("WT_SESSION")
def c(text, code): return text if NO_COLOR else f"\033[{code}m{text}\033[0m"
OK    = lambda t: print(c(f"  ✓  {t}", "32"))
INFO  = lambda t: print(c(f"  →  {t}", "33"))
ERR   = lambda t: print(c(f"  ✗  {t}", "31"))
TITLE = lambda t: print(c(t, "36"))


def banner():
    TITLE("\n  ╔══════════════════════════════════════════╗")
    TITLE("  ║   Agence Numérique Financière            ║")
    TITLE("  ║   46 Agents IA de Trading                ║")
    TITLE("  ╚══════════════════════════════════════════╝\n")


# ── 1. Environnement virtuel ──────────────────────────────────────────
def setup_venv():
    if not PY.exists():
        INFO("Création de l'environnement virtuel (.venv)...")
        subprocess.check_call(
            [sys.executable, "-m", "venv", str(VENV)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        OK("Environnement virtuel créé")


# ── 2. Dépendances ────────────────────────────────────────────────────
def install_deps():
    req = ROOT / "python" / "requirements.txt"
    if not req.exists():
        return

    INFO("Vérification des dépendances...")

    # Vérifier si les packages clés sont déjà installés
    check = subprocess.run(
        [str(PY), "-c", "import fastapi, uvicorn, pandas, yfinance"],
        capture_output=True,
    )
    if check.returncode == 0:
        OK("Dépendances déjà installées")
        return

    INFO("Installation des dépendances (première fois, ~1 min)...")
    result = subprocess.run(
        [str(PY), "-m", "pip", "install", "-q", "-r", str(req)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        ERR("Erreur installation — essai avec les packages de base...")
        subprocess.check_call([
            str(PY), "-m", "pip", "install", "-q",
            "fastapi", "uvicorn[standard]", "python-dotenv",
            "pandas", "numpy", "yfinance", "requests", "httpx", "pydantic", "scikit-learn",
        ])
    OK("Dépendances installées")


# ── 3. Fichier .env ────────────────────────────────────────────────────
def setup_env():
    """Crée python/.env depuis le modèle s'il manque.

    Aucune question n'est posée : l'application est 100 % locale, elle n'a
    besoin d'aucune clé API. Ce fichier ne sert plus qu'aux réglages
    facultatifs (capital de référence, port, plafonds).
    """
    env_path = ROOT / "python" / ".env"
    if env_path.exists():
        return
    example = ROOT / "python" / ".env.example"
    if example.exists():
        shutil.copy(example, env_path)
    else:
        env_path.write_text(
            "CAPITAL_INITIAL=100000\n"
            "BACKEND_HOST=0.0.0.0\n"
            "BACKEND_PORT=8000\n",
            encoding="utf-8",
        )
    OK("Fichier python/.env créé (réglages facultatifs)")


# ── 4. Dossiers nécessaires ────────────────────────────────────────────
def setup_dirs():
    (ROOT / "python" / "data").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)


# ── 5. Vérifier le port ────────────────────────────────────────────────
def port_libre():
    with socket.socket() as s:
        return s.connect_ex(("localhost", PORT)) != 0


# ── 6. Lancer le serveur ──────────────────────────────────────────────
DEJA_LANCE = object()   # sentinelle : succes, mais aucun processus a surveiller


def _agence_repond() -> bool:
    """Vrai si le port est occupe par NOTRE serveur (et non par un tiers)."""
    import json as _json
    import urllib.request
    try:
        # ProxyHandler({}) : sans cela, une variable HTTP_PROXY (fréquente en
        # entreprise) détournerait l'appel vers localhost par le proxy, qui
        # échouerait — l'app se croirait alors « occupée par un tiers ».
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{URL}/api/health", timeout=3) as r:
            data = _json.loads(r.read().decode("utf-8"))
        return data.get("statut") == "ok" and bool(data.get("version"))
    except Exception:
        return False


def start_server():
    if not port_libre():
        # Port occupe : soit notre serveur tourne deja (cas nominal), soit une
        # AUTRE application l'occupe -- auquel cas ouvrir le navigateur y
        # enverrait l'utilisateur par erreur.
        if _agence_repond():
            OK(f"Serveur deja en cours sur {URL}")
            webbrowser.open(URL)
            return DEJA_LANCE
        ERR(f"Le port {PORT} est occupe par une AUTRE application.")
        ERR("Fermez-la puis relancez, ou liberez le port.")
        return None

    INFO(f"Démarrage du serveur sur {URL} ...")

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "python"), str(ROOT)])
    env["PYTHONUNBUFFERED"] = "1"
    # UTF-8 imposé au processus serveur : sinon, sous Windows, la console
    # cp1252 ne peut pas encoder les caractères des messages (→ ✓ €) et
    # l'écriture des journaux lève UnicodeEncodeError.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # Charger les variables d'env du .env
    env_file = ROOT / "python" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())

    # « wb » : le fichier ne reçoit QUE la sortie du processus enfant, qui y
    # écrit ses propres octets (PYTHONIOENCODING=utf-8 plus haut). Le mode
    # texte n'apporterait rien ici et imposerait un encodage au descripteur.
    log_file = open(ROOT / "logs" / "backend.log", "wb")
    try:
        proc = subprocess.Popen(
            [
                str(PY), "-m", "uvicorn", "backend.main:app",
                "--host", "0.0.0.0",
                "--port", str(PORT),
                "--app-dir", str(ROOT),
            ],
            env=env,
            cwd=str(ROOT),
            stdout=log_file,
            stderr=log_file,
        )
    finally:
        # Popen DUPLIQUE le descripteur dans l'enfant : notre copie ne sert
        # plus à rien, et la garder ouverte verrouille le fichier sous Windows
        # — `_afficher_log()` le relit, et le lancement suivant le rouvre en
        # écriture. Même correction que pour le serveur Ollama et le lanceur.
        log_file.close()

    # Attendre que le serveur réponde
    import urllib.request
    print(c("  ", "33"), end="", flush=True)
    for _ in range(40):
        try:
            urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open(f"{URL}/api/health", timeout=2)
            print()
            break
        except Exception:
            if proc.poll() is not None:
                print()
                ERR("Le serveur s'est arrêté. Consultez logs/backend.log")
                _afficher_log()
                return None
            print(c(".", "33"), end="", flush=True)
            time.sleep(1)
    else:
        print()
        ERR("Délai dépassé. Vérifiez logs/backend.log")
        return None

    return proc


def _afficher_log():
    log = ROOT / "logs" / "backend.log"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        print()
        print(c("  ── Dernières lignes du log ──", "31"))
        for line in lines[-15:]:
            print(c(f"  {line}", "31"))
        print()


# ── Main ──────────────────────────────────────────────────────────────
def _pause(msg="\nAppuyez sur Entrée pour fermer..."):
    """Pause avant fermeture de la console (double-clic Windows).

    Protégée : sans terminal interactif (service, tâche planifiée, redirection),
    input() lèverait EOFError et remplacerait le message d'erreur réel par une
    trace incompréhensible.
    """
    try:
        if sys.stdin and sys.stdin.isatty():
            input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


def main():
    banner()

    try:
        setup_venv()
        install_deps()
        setup_env()
        setup_dirs()
    except subprocess.CalledProcessError as e:
        ERR(f"Erreur d'installation : {e}")
        _pause()
        sys.exit(1)

    proc = start_server()
    if proc is DEJA_LANCE:
        # Succes : l'application tournait deja. Sortie en code 0, sinon run.bat
        # afficherait a tort « le lancement a echoue / Python manquant ».
        print()
        print(c(f"  Interface  ->  {URL}", "36"))
        print()
        if sys.platform == "win32":
            _pause()
        return
    if proc is None:
        # Échec visible : sur Windows (double-clic) la console se fermerait
        # instantanément et l'erreur serait illisible.
        if sys.platform == "win32":
            _pause()
        sys.exit(1)

    OK("Agence Numérique Financière démarrée !")
    print()
    print(c(f"  Interface  →  {URL}", "36"))
    print(c(f"  API docs   →  {URL}/docs", "36"))
    print()
    print(c("  Ctrl+C pour arrêter le serveur.", "90"))
    print()

    webbrowser.open(URL)

    try:
        proc.wait()
    except KeyboardInterrupt:
        INFO("Arrêt...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        OK("Serveur arrêté.")

    if sys.platform == "win32":
        _pause()


if __name__ == "__main__":
    main()
