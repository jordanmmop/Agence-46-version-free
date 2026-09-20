"""Application bureau — démarre le serveur FastAPI + fenêtre native pywebview.

Robustesse :
- écran de chargement immédiat, puis bascule vers l'app quand le serveur répond
- choix automatique d'un port libre (évite les conflits avec une ancienne instance)
- erreurs du serveur écrites dans ~/.agence_financiere/app_error.log
  (l'exe n'a pas de console : sans ce fichier, les crashs seraient invisibles)
"""
import os
import sys
import json
import socket
import threading
import time
import traceback

CONFIG_DIR  = os.path.join(os.path.expanduser("~"), ".agence_financiere")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
ERROR_LOG   = os.path.join(CONFIG_DIR, "app_error.log")
# Sortie du PROCESSUS serveur. En version compilée la fenêtre n'a pas de
# console : sans ce fichier, un serveur qui meurt au démarrage ne laisse
# AUCUNE trace, et l'écran d'erreur ne peut rien expliquer.
SERVEUR_LOG = os.path.join(CONFIG_DIR, "serveur.log")


def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def log_erreur(msg: str):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{msg}\n")
    except Exception:
        pass


config = load_config()
# MOTEUR_IA/HERMES_MODEL : le choix entre les deux moteurs locaux (Ollama,
# Hermès). Absents de cette liste, ils étaient enregistrés par l'interface
# mais jamais relus au démarrage — l'application repartait donc sur le moteur
# par défaut à chaque lancement.
for _cle in ("USE_OLLAMA", "OLLAMA_MODEL", "MOTEUR_IA", "HERMES_MODEL"):
    if config.get(_cle):
        os.environ.setdefault(_cle, config[_cle])

if getattr(sys, "frozen", False):
    root = sys._MEIPASS
else:
    root = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "python"))


# ── Mode SERVEUR ──────────────────────────────────────────────────────
# L'application se relance elle-même avec ce drapeau pour faire tourner le
# serveur dans un PROCESSUS SÉPARÉ de la fenêtre (voir _lancer_serveur).
DRAPEAU_SERVEUR = "--serveur-interne"
MODE_SERVEUR = DRAPEAU_SERVEUR in sys.argv


def _port_libre(debut: int = 8765) -> int:
    """Premier port libre à partir de `debut` (une ancienne instance peut occuper le sien).

    Le test se fait sur « 0.0.0.0 », c'est-à-dire la MÊME interface que celle
    qu'utilisera uvicorn : un port libre sur 127.0.0.1 mais occupé sur une
    autre interface ferait échouer le démarrage après coup, et la fenêtre
    resterait bloquée sur l'écran de chargement pendant 180 s."""
    for p in range(debut, debut + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return debut


# Le processus serveur reçoit le port du parent : les deux DOIVENT s'accorder.
PORT = int(os.environ.get("AGENCE_PORT") or 0) or _port_libre()
os.environ["AGENCE_PORT"] = str(PORT)


# Indicateur partagé : passe à True si le serveur meurt (exception d'uvicorn
# OU retour de uvicorn.run). L'écran de chargement peut alors afficher l'erreur
# IMMÉDIATEMENT au lieu d'attendre le délai complet de 180 s.
_serveur_mort = threading.Event()


def start_server():
    try:
        import uvicorn
        # 0.0.0.0 : accessible aussi depuis l'iPhone sur le même Wi-Fi
        # (installation de l'app en PWA — icône sur l'écran d'accueil)
        uvicorn.run(
            "backend.main:app",
            host="0.0.0.0",
            port=PORT,
            log_level="error",
            timeout_keep_alive=75,
        )
        # uvicorn.run ne rend la main que si le serveur s'est arrêté.
        log_erreur("Le serveur uvicorn s'est arrêté prématurément.")
    except Exception:
        log_erreur(traceback.format_exc())
    finally:
        _serveur_mort.set()


def _journal_serveur(max_chars: int = 1200) -> str:
    """Dernières lignes écrites par le processus serveur."""
    try:
        with open(SERVEUR_LOG, encoding="utf-8", errors="replace") as f:
            texte = f.read()
    except Exception:
        return "(journal du serveur indisponible)"
    lignes = [l for l in texte.splitlines() if l.strip()]
    return "\n".join(lignes[-25:])[-max_chars:] or "(journal vide)"


_repli_lance = threading.Event()
_processus_serveur = None       # processus enfant, s'il a pu être lancé


def _demarrer_thread_repli(raison: str):
    """Dernier recours : servir DANS ce processus, comme avant l'isolation.

    L'isolation du serveur protège la fenêtre des blocages MetaTrader — mais
    elle ne doit JAMAIS empêcher l'application de démarrer. Si le processus
    enfant ne part pas (droits, antivirus, exe compilé sans console, chemin
    d'interpréteur inattendu), on retombe sur le fonctionnement historique :
    la fenêtre peut se figer pendant une connexion, mais l'application marche.
    """
    if _repli_lance.is_set():
        return
    _repli_lance.set()
    log_erreur(f"Repli sur le serveur interne : {raison}. La fenêtre peut se "
               f"figer pendant les connexions MetaTrader.")
    # Le processus enfant doit être arrêté AVANT : s'il est encore vivant mais
    # trop lent, il occupe déjà le port. Le serveur de repli, qui écoute sur le
    # MÊME port, échouerait alors sur « adresse déjà utilisée » — et le repli
    # censé sauver le démarrage le condamnerait à coup sûr.
    proc = _processus_serveur
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
    _serveur_mort.clear()
    threading.Thread(target=start_server, daemon=True).start()


def _lancer_serveur():
    """Démarre le serveur DANS UN PROCESSUS SÉPARÉ.

    Il tournait auparavant dans un simple thread de CE processus — celui qui
    fait aussi vivre la fenêtre native. Or la librairie MetaTrader5 est une
    extension C : pendant une connexion au courtier, elle bloque plusieurs
    dizaines de secondes sans rendre l'interpréteur, ce qui privait la boucle
    de messages de la fenêtre. Windows affichait alors « Agence Numérique
    Financière (Ne répond pas) », et rien ne permettait de reprendre la main.

    Dans un processus distinct, MetaTrader5 ne peut plus figer la fenêtre :
    au pire une requête met du temps à répondre, l'application reste utilisable.
    Cela protège aussi contre un blocage dur ou un plantage de la librairie,
    qui emportait jusqu'ici toute l'application.
    """
    import subprocess
    # sys.executable : l'exe lui-même en version compilée, l'interpréteur
    # sinon. Le drapeau lui dit de ne PAS rouvrir de fenêtre.
    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        cmd.append(os.path.abspath(__file__))
    cmd.append(DRAPEAU_SERVEUR)

    env = os.environ.copy()
    env["AGENCE_PORT"] = str(PORT)
    env["PYTHONUTF8"] = "1"              # console Windows cp1252 (cf. backend)
    env["PYTHONIOENCODING"] = "utf-8"

    # SORTIE REDIRIGÉE VERS UN FICHIER — indispensable, et pas seulement pour
    # le diagnostic : en version compilée la fenêtre est bâtie sans console
    # (console=False), donc SES propres descripteurs de sortie sont invalides.
    # Un enfant qui en hérite meurt dès la première écriture de journal
    # d'uvicorn, sans laisser la moindre trace — c'est exactement ce qui
    # produisait « Le serveur n'a pas démarré ».
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        sortie = open(SERVEUR_LOG, "w", encoding="utf-8", errors="replace")
    except Exception:
        sortie = None
    kwargs = {
        "env": env,
        "cwd": root,
        "stdin": subprocess.DEVNULL,
        "stdout": sortie or subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception:
        log_erreur("Lancement du serveur en processus séparé impossible :\n"
                   + traceback.format_exc())
        _demarrer_thread_repli("le processus serveur n'a pas pu être lancé")
        return None
    finally:
        # Le descripteur est DUPLIQUÉ dans l'enfant : on referme notre copie,
        # sinon le fichier reste verrouillé sous Windows.
        if sortie is not None:
            sortie.close()

    # Le serveur est un processus enfant : il doit mourir avec la fenêtre,
    # sinon il garde le port et l'utilisateur ne peut plus relancer l'app.
    import atexit

    def _arreter():
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    atexit.register(_arreter)

    # Surveillance : si le processus meurt AVANT d'avoir servi la première
    # requête, on ne déclare pas l'application en panne — on REPART sur le
    # thread interne. L'isolation du serveur protège la fenêtre des blocages
    # MetaTrader ; elle ne doit jamais empêcher l'application de démarrer.
    def _surveiller():
        code = proc.wait()
        if _serveur_pret():
            return                       # arrêt normal (fermeture de la fenêtre)
        log_erreur(f"Le processus serveur s'est arrêté (code {code}).\n"
                   f"Journal : {_journal_serveur()}")
        _demarrer_thread_repli(f"le processus serveur s'est arrêté (code {code})")
    threading.Thread(target=_surveiller, daemon=True).start()
    global _processus_serveur
    _processus_serveur = proc
    return proc


if MODE_SERVEUR:
    # Processus enfant : uniquement le serveur, aucune fenêtre.
    start_server()
    sys.exit(0)

_lancer_serveur()

# L'écran de choix (IA locale / clé API) s'affiche à CHAQUE lancement.
# Il pré-sélectionne le mode actuel — un clic suffit pour continuer.
url = f"http://127.0.0.1:{PORT}/setup"


def _serveur_pret(timeout_s: float = 2.0) -> bool:
    import urllib.request
    try:
        # ProxyHandler({}) : une variable HTTP_PROXY (fréquente en entreprise)
        # détournerait cet appel à 127.0.0.1 vers le proxy, qui échouerait.
        # La fenêtre restait alors 180 s sur l'écran de chargement puis
        # annonçait un serveur mort — alors qu'il répondait normalement.
        # Même convention que start.py et backend/main.py.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{PORT}/api/health", timeout=timeout_s):
            return True
    except Exception:
        return False


def _attendre_serveur(max_s: float = 180.0) -> bool:
    """Le premier démarrage d'un exe peut prendre 30-60s (chargement des 46 agents)."""
    fin = time.time() + max_s
    while time.time() < fin:
        if _serveur_pret():
            return True
        if _serveur_mort.is_set():
            # Le serveur a planté : inutile d'attendre 180 s, l'erreur est déjà
            # dans le journal — on bascule tout de suite sur l'écran d'erreur.
            return False
        time.sleep(1.0)
    return False


LOADING_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{background:#0a0e1a;color:#e0e0e0;font-family:system-ui,sans-serif;display:flex;
flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0}
.spin{width:44px;height:44px;border:4px solid #1e2a45;border-top-color:#64b5f6;
border-radius:50%;animation:r 0.9s linear infinite;margin-bottom:24px}
@keyframes r{to{transform:rotate(360deg)}}
h1{font-size:18px;color:#64b5f6;margin:0 0 8px}p{font-size:13px;color:#7a8aaa;margin:4px}
</style></head><body>
<div class="spin"></div>
<h1>Agence Num&eacute;rique Financi&egrave;re</h1>
<p>D&eacute;marrage des 46 agents IA et de leurs assistants...</p>
<p>Le premier lancement peut prendre jusqu'&agrave; une minute.</p>
</body></html>"""

def _ecran_erreur() -> str:
    """Écran d'échec affichant la CAUSE, pas seulement un chemin de fichier.

    L'ancien écran se contentait de renvoyer vers un journal : l'utilisateur
    voyait « Le serveur n'a pas démarré » sans le moindre élément exploitable,
    et devait aller ouvrir un fichier pour espérer comprendre. Le motif réel
    est désormais affiché à l'écran.
    """
    import html as _html
    journal = _html.escape(_journal_serveur())
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{background:#0a0e1a;color:#e0e0e0;font-family:system-ui,sans-serif;
margin:0;padding:32px;display:flex;flex-direction:column;align-items:center}}
h1{{font-size:19px;color:#ff6b6b;margin:0 0 14px}}
p{{font-size:13px;color:#9aadcc;margin:6px 0;max-width:640px;text-align:center}}
code{{background:#131929;padding:2px 8px;border-radius:4px;font-size:12px;color:#64b5f6}}
pre{{background:#0d1220;border:1px solid #1e2a45;border-radius:8px;padding:14px;
font-size:11.5px;color:#c7d3e6;max-width:760px;width:100%;overflow:auto;
white-space:pre-wrap;text-align:left;margin-top:18px}}
</style></head><body>
<h1>Le serveur n'a pas d&eacute;marr&eacute;</h1>
<p>Voici ce qu'il a &eacute;crit avant de s'arr&ecirc;ter :</p>
<pre>{journal}</pre>
<p>Journaux complets :<br><code>{SERVEUR_LOG}</code><br><code>{ERROR_LOG}</code></p>
<p>Relancez l'application. Si le probl&egrave;me persiste, red&eacute;marrez
votre ordinateur : un ancien processus occupe peut-&ecirc;tre le port
{PORT}.</p>
</body></html>"""

try:
    import webview
except ImportError:
    webview = None


def _ouvrir_dans_le_navigateur(raison: str = "") -> None:
    """Repli : servir l'application dans le navigateur par défaut.

    La fenêtre native de pywebview s'appuie sous Windows sur le « Microsoft
    Edge WebView2 Runtime ». Il est présent d'origine sur Windows 11 et sur un
    Windows 10 à jour, mais PAS sur une machine fraîchement installée —
    exactement le cas d'un utilisateur qui vient d'installer l'application.
    `webview.start()` lève alors une exception, et un exe compilé sans console
    disparaissait sans un mot : double-clic, rien à l'écran, rien à comprendre.
    L'installeur pose ce composant quand il manque ; ce repli couvre les cas
    où il n'a pas pu (installation hors ligne, machine d'entreprise verrouillée).
    """
    import webbrowser
    if raison:
        log_erreur(f"Fenêtre native indisponible ({raison}). "
                   f"Ouverture dans le navigateur : {url}")
    if _attendre_serveur():
        webbrowser.open(url)
    else:
        _demarrer_thread_repli("aucune réponse du processus serveur")
        if _attendre_serveur(120.0):
            webbrowser.open(url)
        else:
            print(f"[ERREUR] Le serveur n'a pas démarré — voir {ERROR_LOG}")
            return
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if webview:
    window = webview.create_window(
        title="Agence Numérique Financière",
        html=LOADING_HTML,
        width=1440,
        height=900,
        min_size=(900, 600),
        resizable=True,
    )

    def _basculer_quand_pret():
        if _attendre_serveur():
            window.load_url(url)
            return
        # Le processus serveur n'a pas répondu : avant d'annoncer une panne,
        # on tente le mode historique (serveur dans ce processus). Un
        # démarrage réussi vaut mieux qu'un écran d'erreur, même au prix du
        # risque de figeage pendant les connexions MetaTrader.
        log_erreur(f"Le serveur n'a pas répondu sur le port {PORT}.\n"
                   f"Journal du serveur :\n{_journal_serveur()}")
        _demarrer_thread_repli("aucune réponse du processus serveur")
        if _attendre_serveur(120.0):
            window.load_url(url)
            return
        window.load_html(_ecran_erreur())

    try:
        webview.start(_basculer_quand_pret)
    except Exception:
        # WebView2 absent, moteur graphique refusé, session sans bureau :
        # l'application doit s'ouvrir quand même.
        log_erreur(traceback.format_exc())
        _ouvrir_dans_le_navigateur("webview.start a échoué")
else:
    # pywebview absent : on ouvre le navigateur par défaut à la place
    print("[INFO] pywebview non installé — ouverture dans le navigateur.")
    print("       Pour la fenêtre native : python -m pip install pywebview")
    print(f"       App disponible sur : {url}")
    _ouvrir_dans_le_navigateur()
