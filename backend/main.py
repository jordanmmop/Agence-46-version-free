"""Backend FastAPI - Agence Numérique Financière"""
import sys
import os

# Support PyInstaller frozen exe (sys._MEIPASS) and normal execution
if getattr(sys, "frozen", False):
    _ROOT = sys._MEIPASS
else:
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "python"))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from typing import Optional, Dict, Any
from collections import deque
from threading import Lock
import json
import logging
import logging.config
from datetime import datetime

# Console Windows en cp1252 (« charmap ») : elle ne sait PAS encoder les
# caractères que l'application journalise couramment (→ ✓ ✗ € émojis).
# Sans cette reconfiguration, logger.info() lève UnicodeEncodeError, et cette
# exception REMONTE : elle interrompait le cycle de l'auto-trader (les symboles
# suivants n'étaient plus analysés) et s'affichait comme
# « 'charmap' codec can't encode character '→' » dans le journal d'erreurs.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # flux non reconfigurable (exe sans console, redirection)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Numéro lu dans le fichier `VERSION` de la racine — la MÊME source que celle
# de l'exe compilé et de l'installeur Windows. Recopié ici, il aurait fini par
# annoncer une version différente de celle qu'affiche « Applications
# installées » après une mise à jour.
try:
    from utils.version import version as _version
    APP_VERSION = _version()
except Exception:                       # arborescence python/ indisponible
    APP_VERSION = "0.0.0-dev"

# ── Journal des erreurs en mémoire ────────────────────────────────────
# L'exe Windows est compilé SANS console (console=False) : tout ce que le
# serveur écrit sur stderr part dans le vide. Quand une route échouait,
# l'utilisateur ne voyait qu'un bandeau rouge « Erreur interne du serveur »
# et il n'existait AUCUN moyen, depuis l'application, de savoir laquelle et
# pourquoi. On conserve donc les dernières erreurs ici, consultables par
# /api/diagnostic (authentifié) — c'est le seul endroit où la trace existe
# encore une fois l'application compilée.
_ERREURS_RECENTES: Optional[deque] = None


def _journaliser_erreur(source: str, message: str, exc: BaseException = None):
    """Ajoute une erreur au journal consultable. Ne lève jamais."""
    try:
        trace = ""
        if exc is not None:
            import traceback as _tb
            trace = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        _ERREURS_RECENTES.append({
            "horodatage": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "message": message[:2000],
            "trace": trace[-4000:],
        })
    except Exception:
        pass                  # un journal défaillant ne doit rien casser


def _init_journal_erreurs(taille: int = 50):
    global _ERREURS_RECENTES
    _ERREURS_RECENTES = deque(maxlen=taille)

    class _Collecteur(logging.Handler):
        """Récupère les erreurs journalisées AILLEURS que dans le gestionnaire
        global (routes, auto-trader, gestionnaire MT5)."""

        def emit(self, record):
            try:
                if record.levelno < logging.ERROR:
                    return
                # Déjà enregistrée explicitement par le gestionnaire global :
                # ne pas la compter deux fois.
                if getattr(record, "deja_journalise", False):
                    return
                exc = record.exc_info[1] if record.exc_info else None
                _journaliser_erreur(record.name, record.getMessage(), exc)
            except Exception:
                pass

    collecteur = _Collecteur()
    # Niveau fixé sur le HANDLER, et non hérité du logger racine : une
    # application qui hausse le niveau global (ou une suite de tests qui tait
    # ses journaux) cesserait sinon d'alimenter le diagnostic — précisément
    # quand on en a besoin. Le gestionnaire global, lui, écrit directement
    # dans le journal sans passer par logging.
    collecteur.setLevel(logging.ERROR)
    logging.getLogger().addHandler(collecteur)


_init_journal_erreurs()

app = FastAPI(
    title="Agence Numérique Financière",
    description="46 agents de trading IA (45 spécialisés + 1 Chef d'Orchestre) + 46 assistants IA locaux",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Référence courte, commune au journal et à la réponse : elle permet de
    # relier le bandeau rouge vu à l'écran à la trace complète de
    # /api/diagnostic, même après plusieurs erreurs différentes.
    import uuid
    reference = uuid.uuid4().hex[:6].upper()
    message = f"Erreur non gérée [{request.url.path}] (réf. {reference}) : {exc}"
    # Enregistrement DIRECT, indépendant de la configuration de logging : la
    # trace doit exister dans /api/diagnostic même si les journaux sont
    # coupés, redirigés ou montés en niveau.
    _journaliser_erreur(f"route {request.url.path}", message, exc)
    logger.error(message, exc_info=True, extra={"deja_journalise": True})

    reponse = {"error": "Erreur interne du serveur",
               "path": str(request.url.path),
               "reference": reference}

    # Le détail n'est donné qu'à une session AUTHENTIFIÉE — c'est-à-dire au
    # propriétaire de l'application, jamais à un visiteur du réseau local.
    # Sans lui, l'utilisateur ne pouvait rien faire d'un message qui ne
    # nommait ni la cause ni l'endroit : « Erreur interne du serveur » était
    # littéralement toute l'information disponible.
    try:
        from utils.auth import auth_active, token_valide, COOKIE
        authentifie = (not auth_active()
                       or token_valide(request.cookies.get(COOKIE, "")))
    except Exception:
        authentifie = False
    if authentifie:
        reponse["detail"] = (f"{type(exc).__name__}: {exc} "
                             f"(réf. {reference} — Réglages ⚙️ → Diagnostic)")

    return JSONResponse(status_code=500, content=reponse)


# ═══════════════════════════ AUTHENTIFICATION ══════════════════════════════
# DÉSACTIVÉE PAR DÉFAUT — l'application ne demande aucun code au lancement.
# `APP_AUTH=on` (ou `APP_PASSWORD=<code>`) rétablit la protection ; tout le
# mécanisme ci-dessous reste en place pour ce cas. Voir python/utils/auth.py
# pour ce que cette protection couvrait et comment s'en passer sans risque.
_AUTH_PUBLIC = {"/login", "/api/login", "/api/health", "/manifest.json",
                "/sw.js", "/favicon.ico"}
_AUTH_PUBLIC_PREFIX = ("/icons/",)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    from utils.auth import auth_active, token_valide, COOKIE
    if not auth_active():
        return await call_next(request)
    path = request.url.path
    if (request.method == "OPTIONS" or path in _AUTH_PUBLIC
            or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIX)):
        return await call_next(request)
    if token_valide(request.cookies.get(COOKIE, "")):
        return await call_next(request)
    # Non authentifié : API → 401, page → redirection vers /login
    if path.startswith("/api/") or path.startswith("/setup/"):
        return JSONResponse(status_code=401,
                            content={"error": "Authentification requise", "auth": True})
    from urllib.parse import quote
    return RedirectResponse(url=f"/login?next={quote(path)}", status_code=302)

_orchestrateur = None
_derniere_analyse = None
_lock = Lock()


def normaliser_symboles(brut, maximum: int = 10) -> list:
    """Liste de symboles propre, quelle que soit la forme reçue.

    Politique UNIQUE pour /api/analyser, /api/auto-trader/start et
    /api/preflight — les trois la ré-implémentaient différemment :

    - une CHAÎNE vaut UN symbole. Sans cela `"BAC"` était itérée caractère
      par caractère : B, A et C sont de VRAIS tickers, les données réelles
      arrivaient, le garde-fou « source_simulee » ne se déclenchait pas, et
      trois titres non voulus étaient analysés (et tradés, sur le chemin
      auto-trader).
    - un nombre, un booléen, un objet → aucun symbole (et non une exception :
      `[str(s) for s in 42]` levait un TypeError rendu en 500 « Erreur
      interne », sans indiquer à l'utilisateur ce qui n'allait pas).
    - les éléments non textuels d'une liste sont ignorés, pas convertis :
      `str({"a": 1})` produirait un « symbole » absurde envoyé au courtier.
    """
    if isinstance(brut, str):
        brut = [brut]
    elif isinstance(brut, (list, tuple)):
        pass
    else:
        return []
    from config import vers_symbole_yahoo
    propres = []
    for s in brut:
        if not isinstance(s, str):
            continue
        # Traduit le nom AFFICHÉ par l'interface (celui du Market Watch
        # MetaTrader : BTCUSD, AUDUSD) vers le format Yahoo, seule source des
        # cours. Sans cela, la saisie la plus naturelle — recopier ce que
        # l'écran montre — faisait échouer le téléchargement et l'application
        # travaillait sur des prix inventés.
        s = vers_symbole_yahoo(s)
        if s and s not in propres:
            propres.append(s)
    return propres[:maximum]


@app.on_event("startup")
async def _demarrage_moteurs_embarques():
    """Démarre le moteur IA embarqué choisi et prépare MetaTrader 5.

    Tout ce qui est nécessaire au fonctionnement est LIVRÉ avec l'application
    (runtime/) : il n'y a plus d'installation à faire au premier lancement,
    seulement des services à démarrer.
    """
    import threading

    def _boot():
        try:
            from utils import moteur_ia
            moteur_ia.demarrer_si_possible()
        except Exception as e:
            logger.warning(f"Moteur IA embarqué non démarré : {e}")
        # MetaTrader 5 : démarrer le terminal EMBARQUÉ (ou, à défaut, lancer
        # l'installeur embarqué). C'est le pendant de l'IA locale —
        # l'application est livrée complète, l'utilisateur n'a rien à aller
        # chercher. Fait AVANT la reconnexion : elle a besoin d'un terminal.
        try:
            _preparer_mt5_embarque()
        except Exception as e:
            logger.warning(f"Préparation MetaTrader 5 : {e}")
        try:
            _reconnexion_auto_si_demandee()
        except Exception as e:
            logger.warning(f"Reconnexion auto : {e}")
        # Purge des signaux périmés : la boucle auto-trader purge une fois par
        # jour, mais un utilisateur qui n'emploie que « Analyser » n'en
        # bénéficierait jamais — chaque clic écrit pourtant 46 signaux par
        # symbole. On repasse donc aussi à chaque démarrage.
        try:
            from utils.auto_trader import purger_base
            purger_base()
        except Exception as e:
            logger.warning(f"Purge de la base : {e}")

    threading.Thread(target=_boot, daemon=True).start()


def _reconnexion_auto_si_demandee():
    """Reconnecte le compte AvaTrade au démarrage, si l'utilisateur l'a voulu.

    `mt5_attacher` compte AUTANT que des identifiants mémorisés : c'est le
    choix « reprendre le compte déjà ouvert dans le terminal », qui
    n'enregistre volontairement aucun mot de passe. Le test ne portait que sur
    les identifiants : ces utilisateurs n'étaient donc jamais reconnectés au
    démarrage et devaient rouvrir la fenêtre de connexion à chaque lancement,
    sans qu'aucun message ne l'explique.
    """
    from utils import app_config
    from utils.mt5_manager import get_mt5_manager
    mt5 = get_mt5_manager()
    if (mt5.identifiants_enregistres().get("enregistres")
            or app_config.get("mt5_attacher")):
        return mt5.reconnexion_auto()
    return {"success": False, "error": "Aucune reconnexion demandée"}


def _preparer_mt5_embarque():
    """Rend MetaTrader 5 utilisable au démarrage, sans rien demander.

    Deux cas, du meilleur au moins bon :

    1. Un terminal PORTABLE est embarqué (`runtime/mt5/terminal/`) : il n'y a
       rien à installer, on le DÉMARRE. C'est le cas d'un exe compilé sous
       Windows par build_exe.bat.
    2. Seul l'installeur est embarqué : on le lance UNE fois, en silence.

    `MT5_AUTO_INSTALL=false` désactive le second cas — pas le premier :
    démarrer un terminal déjà livré n'installe rien sur la machine.
    """
    import platform
    from utils.mt5_manager import get_mt5_manager
    gestionnaire = get_mt5_manager()

    # ── 1. Terminal portable livré : le démarrer, point final.
    if gestionnaire.terminal_embarque():
        if gestionnaire._terminal_repond(timeout_ms=3000):
            logger.info("[MT5] Terminal déjà actif")
            return
        resultat = gestionnaire.demarrer_terminal_embarque()
        if resultat.get("success"):
            logger.info("[MT5] Terminal embarqué démarré — prêt à recevoir "
                        "vos identifiants")
        else:
            logger.warning(f"[MT5] Terminal embarqué : {resultat.get('error')}")
        return

    # ── 2. Repli : installer depuis l'installeur embarqué, une seule fois.
    if platform.system() != "Windows":
        return
    if os.getenv("MT5_AUTO_INSTALL", "true").lower() in ("0", "false", "no"):
        return
    from utils import app_config
    if app_config.get("mt5_auto_install_tente"):
        return
    from utils.mt5_installer import get_mt5_installer
    inst = get_mt5_installer()
    if inst.terminal_installe() or inst.setup_embarque() is None:
        return
    app_config.set("mt5_auto_install_tente", True)
    logger.info("[MT5] Installation automatique depuis l'installeur embarqué")
    inst.installer_async()


def get_orchestrateur():
    global _orchestrateur
    with _lock:
        if _orchestrateur is None:
            from agents.orchestrateur import ChefOrchestre
            _orchestrateur = ChefOrchestre()
        return _orchestrateur


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connexion — Agence Numérique Financière</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0c10;color:#dde3ec;font-family:system-ui,-apple-system,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}
.card{background:#10131a;border:1px solid #232a37;border-radius:14px;padding:44px 36px;
max-width:380px;width:100%;box-shadow:0 24px 64px rgba(0,0,0,.6);text-align:center}
.logo{font-size:38px;margin-bottom:14px}
h1{color:#5b8def;font-size:19px;margin-bottom:6px}
p{color:#7a8aaa;font-size:13px;margin-bottom:28px}
input{width:100%;padding:14px;background:#151a23;border:1px solid #232a37;border-radius:10px;
color:#e0e0e0;font-size:18px;text-align:center;letter-spacing:4px;font-family:monospace}
input:focus{outline:none;border-color:#5b8def}
button{width:100%;padding:14px;margin-top:16px;background:#5b8def;
color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#6d9af1}
.err{background:#1a0a0a;border:1px solid #5a1a1a;color:#ff6b6b;padding:10px;border-radius:8px;
margin-top:14px;font-size:13px;display:none}
</style></head><body>
<div class="card">
<div class="logo">🔒</div>
<h1>Agence Numérique Financière</h1>
<p>Entrez votre code d'accès</p>
<input type="password" id="pw" inputmode="numeric" placeholder="••••••" autocomplete="current-password" autofocus>
<button onclick="login()">Se connecter</button>
<div class="err" id="err"></div>
</div>
<script>
function nextParam(){
  // N'autoriser QUE des chemins internes (redirection ouverte / javascript: sinon)
  const n=new URLSearchParams(location.search).get('next')||'/';
  return (n==='/'||/^\/[^\/]/.test(n))?n:'/';
}
async function login(){
  const pw=document.getElementById('pw').value.trim();
  const err=document.getElementById('err');err.style.display='none';
  const btn=document.querySelector('button');btn.disabled=true;btn.textContent='Connexion...';
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pw})});
    const d=await r.json();
    if(d.success){location.href=nextParam();return;}
    err.textContent='Code incorrect.';err.style.display='block';
  }catch(e){err.textContent='Erreur réseau.';err.style.display='block';}
  btn.disabled=false;btn.textContent='Se connecter';
}
document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')login();});
</script></body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    # Aucun code demandé : cette page n'aurait rien à valider. Un signet ou un
    # ancien raccourci pointant ici ne doit pas afficher un formulaire inerte
    # — il ramène directement à l'application.
    from utils.auth import auth_active
    if not auth_active():
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(_LOGIN_HTML)


@app.post("/api/login")
async def api_login(request: Request, body: Dict[str, Any] = Body(...)):
    from utils.auth import (verifier_mot_de_passe, creer_token, COOKIE,
                            login_bloque, enregistrer_echec, reinitialiser_echecs)
    # Compteur par IP : un tiers du réseau ne peut pas verrouiller le
    # propriétaire légitime en épuisant volontairement les essais.
    ip = getattr(request.client, "host", None)
    bloque, restant = login_bloque(ip)
    if bloque:
        return JSONResponse(status_code=429, content={
            "success": False,
            "error": f"Trop de tentatives — réessayez dans {restant} s."})
    # PBKDF2 (200 000 itérations) = ~100 ms de CPU : déporté en thread pour ne
    # pas figer la boucle d'événements (le frontend sonde en continu).
    import asyncio
    ok = await asyncio.to_thread(verifier_mot_de_passe, body.get("password", ""))
    if not ok:
        enregistrer_echec(ip)
        return JSONResponse(status_code=401, content={"success": False, "error": "Code incorrect"})
    reinitialiser_echecs(ip)
    resp = JSONResponse({"success": True})
    resp.set_cookie(COOKIE, creer_token(30), max_age=30 * 86400,
                    httponly=True, samesite="lax", path="/")
    return resp


@app.post("/api/logout")
async def api_logout():
    from utils.auth import COOKIE
    resp = JSONResponse({"success": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.post("/api/password")
async def api_password(body: Dict[str, Any] = Body(...)):
    """Changer le code d'accès (l'utilisateur est déjà authentifié via le middleware)."""
    from utils.auth import (verifier_mot_de_passe, changer_mot_de_passe,
                            creer_token, auth_active, COOKIE)
    if not auth_active():
        return {"success": False,
                "error": ("L'application ne demande aucun code d'accès. "
                          "Pour en utiliser un, démarrez-la avec APP_AUTH=on.")}
    if not verifier_mot_de_passe(body.get("actuel", "")):
        return {"success": False, "error": "Code actuel incorrect"}
    if os.getenv("APP_PASSWORD"):
        return {"success": False,
                "error": ("Le code est fixé par la variable d'environnement APP_PASSWORD "
                          "et ne peut pas être changé ici. Modifiez APP_PASSWORD.")}
    if len(str(body.get("nouveau", "")).strip()) < 4:
        return {"success": False, "error": "Nouveau code trop court (min 4)"}
    if not changer_mot_de_passe(body.get("nouveau", "")):
        return {"success": False, "error": "Changement impossible"}
    # changer_mot_de_passe a fait tourner le secret de session : toutes les
    # sessions (y compris celle-ci) sont invalidées. On ré-émet un cookie frais
    # à l'utilisateur qui vient de changer son code, pour ne pas le déconnecter.
    resp = JSONResponse({"success": True, "sessions_invalidees": True})
    resp.set_cookie(COOKIE, creer_token(30), max_age=30 * 86400,
                    httponly=True, samesite="lax", path="/")
    return resp


@app.get("/api/health")
async def health():
    return {"statut": "ok", "version": APP_VERSION, "timestamp": datetime.now().isoformat()}


@app.get("/", response_class=HTMLResponse)
async def root():
    # _ROOT (et non un recalcul depuis __file__) : dans l'exe PyInstaller, la
    # racine est sys._MEIPASS. C'est la MÊME base que celle des montages
    # statiques plus bas — les deux ne peuvent plus diverger.
    frontend_path = os.path.join(_ROOT, "frontend", "index.html")
    if os.path.exists(frontend_path):
        with open(frontend_path, encoding="utf-8") as f:
            return f.read()
    return "<h1>Agence Numérique Financière - API Active</h1>"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return await root()


@app.get("/api/status")
async def status():
    """Battement de cœur du tableau de bord — interrogé toutes les secondes.

    Cette route ne doit JAMAIS répondre 500. Elle sert à dire « le serveur
    est là », et le tableau de bord peint un bandeau rouge en travers de
    l'écran dès qu'elle échoue : une seule valeur non sérialisable au fond du
    portefeuille rendait donc l'application entière inutilisable, sans nommer
    la cause. Les parties qui échouent sont désormais isolées et rapportées
    dans `erreurs`, le reste continue de s'afficher.
    """
    from agents import TOUS_LES_AGENTS
    reponse: Dict[str, Any] = {
        "statut": "actif",
        "version": APP_VERSION,
        "nb_agents": len(TOUS_LES_AGENTS),
        "nb_total": len(TOUS_LES_AGENTS) + 1,
        "nb_assistants": len(TOUS_LES_AGENTS) + 1,
        "timestamp": datetime.now().isoformat(),
    }
    # L'interface s'en sert pour n'afficher la section « Sécurité » des
    # réglages que lorsqu'un code existe réellement : proposer « changer le
    # code » ou « se déconnecter » alors qu'il n'y a rien à changer ni de
    # session à fermer ne mène qu'à des messages d'erreur incompréhensibles.
    try:
        from utils.auth import auth_active
        reponse["auth"] = auth_active()
    except Exception:
        reponse["auth"] = False
    try:
        reponse["orchestrateur"] = get_orchestrateur().to_dict()
    except Exception as e:
        logger.error(f"/api/status : orchestrateur indisponible ({e})", exc_info=True)
        reponse["statut"] = "degrade"
        reponse["orchestrateur"] = None
        reponse["erreurs"] = [f"Chef d'Orchestre indisponible — {type(e).__name__}: {e}"]
    return reponse


@app.get("/api/assistants")
async def lister_assistants():
    """Les 46 assistants IA locaux (un par agent, orchestrateur inclus).

    Un assistant qui ne sait pas se décrire ne doit pas faire disparaître les
    45 autres de l'écran : il est remplacé par une ligne en erreur, visible
    comme telle dans le tableau de bord.
    """
    from agents import TOUS_LES_AGENTS
    assistants = []
    try:
        assistants.append(get_orchestrateur().assistant.to_dict())
    except Exception as e:
        logger.error(f"/api/assistants : orchestrateur ({e})", exc_info=True)
    for agent in TOUS_LES_AGENTS:
        try:
            assistants.append(agent.assistant.to_dict())
        except Exception as e:
            logger.error(f"/api/assistants : {getattr(agent, 'NOM', agent)} ({e})",
                         exc_info=True)
            assistants.append({"id": getattr(agent, "ID", "?"),
                               "nom": getattr(agent, "NOM", "?"),
                               "statut": "erreur",
                               "erreur": f"{type(e).__name__}: {e}"})
    return {"assistants": assistants, "total": len(assistants)}


@app.get("/api/agents")
async def lister_agents():
    try:
        from agents import TOUS_LES_AGENTS, AGENTS_PAR_GROUPE
        chef = get_orchestrateur()
        return {
            "orchestrateur": chef.to_dict(),
            "agents": [a.to_dict() for a in TOUS_LES_AGENTS],
            "par_groupe": {
                groupe: [a.to_dict() for a in agents]
                for groupe, agents in AGENTS_PAR_GROUPE.items()
            },
            "total": len(TOUS_LES_AGENTS) + 1,
        }
    except Exception as e:
        logger.error(f"Erreur /api/agents : {e}")
        raise HTTPException(500, "Erreur lors du chargement des agents")


@app.get("/api/agents/{agent_id}")
async def agent_detail(agent_id: str):
    from agents import AGENTS_PAR_ID
    chef = get_orchestrateur()
    if agent_id == chef.ID:
        return chef.to_dict()
    agent = AGENTS_PAR_ID.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} non trouvé")
    return agent.to_dict()


@app.post("/api/analyser")
async def lancer_analyse(
    background_tasks: BackgroundTasks,
    request: Request,
    async_mode: bool = False,
):
    global _derniere_analyse
    from config import SYMBOLES_DEFAULT

    # Accept bare JSON array ["BTC-USD", ...] OR object {"symboles": [...]}
    try:
        body = await request.json()
        if isinstance(body, list):
            symboles = body
        elif isinstance(body, dict):
            symboles = body.get("symboles") or []
        else:
            symboles = []
    except Exception:
        symboles = []

    symboles_a_analyser = normaliser_symboles(symboles) or list(SYMBOLES_DEFAULT[:3])

    if async_mode:
        background_tasks.add_task(_analyser_async, symboles_a_analyser)
        return {"message": "Analyse lancée en arrière-plan", "symboles": symboles_a_analyser}

    chef = get_orchestrateur()
    try:
        # IMPÉRATIF : orchestrer() est BLOQUANT (46 agents + synthèses LLM, et
        # il attend _orch_lock si la boucle auto-trader tourne déjà). L'exécuter
        # directement dans cette coroutine gèlerait la boucle d'événements —
        # donc TOUT le serveur (statut, positions, bouton « Arrêter ») pendant
        # une à deux minutes, avec des positions réelles ouvertes.
        # On le déporte dans un thread : le serveur reste réactif.
        import asyncio
        rapport = await asyncio.to_thread(chef.orchestrer, symboles_a_analyser)
    except Exception as e:
        logger.error(f"Erreur orchestration: {e}")
        raise HTTPException(500, "Erreur lors de l'analyse")
    _derniere_analyse = rapport

    # Persister le portfolio après chaque analyse
    try:
        from utils.database import Database
        Database().sauver_portfolio(rapport.get("portfolio", {}))
    except Exception as e:
        logger.warning(f"Portfolio non sauvegardé: {e}")

    # L'analyse manuelle est PUREMENT consultative : elle NE PASSE AUCUN ORDRE.
    # Le passage d'ordres réels est confié exclusivement à la boucle
    # AutoTrader (« Démarrer le trading »), chemin d'exécution UNIQUE et
    # sérialisé. Cela évite : (1) qu'un simple clic sur « Analyser » ouvre des
    # positions réelles à l'insu de l'utilisateur, (2) deux chemins d'ordres
    # concurrents (course, positions dupliquées) entre l'endpoint et la boucle.
    return rapport


def _analyser_async(symboles: list):
    """Tâche de fond de /api/analyser?async_mode=true.

    IMPÉRATIF : fonction SYNCHRONE. Starlette exécute une tâche de fond
    `async def` DANS la boucle d'événements — orchestrer() étant bloquant
    (46 agents + synthèses LLM, une à deux minutes), tout le serveur se
    figeait : statut, positions et bouton « Arrêter » ne répondaient plus
    alors que des positions réelles pouvaient être ouvertes. Déclarée `def`,
    la tâche part dans le pool de threads et le serveur reste réactif —
    même garantie que le chemin synchrone (asyncio.to_thread).
    """
    global _derniere_analyse
    chef = get_orchestrateur()
    try:
        _derniere_analyse = chef.orchestrer(symboles)
    except Exception as e:
        logger.error(f"Analyse en arrière-plan échouée : {e}", exc_info=True)


@app.get("/api/analyse/derniere")
async def derniere_analyse():
    if not _derniere_analyse:
        raise HTTPException(404, "Aucune analyse effectuée. Lancez d'abord POST /api/analyser")
    return _derniere_analyse


@app.get("/api/portfolio")
async def portfolio():
    chef = get_orchestrateur()
    return chef.portfolio.to_dict()


@app.get("/api/signaux")
def signaux(limite: int = 50, symbole: Optional[str] = None):
    limite = max(1, min(1000, limite))   # LIMIT négatif = table entière en SQLite
    from utils.database import Database
    db = Database()
    lignes = db.lire_signaux(limite, symbole)
    return {"signaux": lignes, "total": len(lignes)}


@app.get("/api/signaux/stats")
async def stats_signaux():
    from utils.database import Database
    db = Database()
    return {"stats": db.lire_stats_agents()}


@app.get("/api/portfolio/historique")
def portfolio_historique(limite: int = 200):
    limite = max(1, min(2000, limite))
    from utils.database import Database
    db = Database()
    historique = db.lire_portfolio_historique(limite)
    return {"historique": list(reversed(historique)), "total": len(historique)}


@app.get("/api/marche/{symbole}")
async def donnees_marche(symbole: str, timeframe: str = "1h"):
    # Téléchargement réseau : déporté en thread, sinon la boucle d'événements
    # (donc tout le serveur) se fige le temps de la requête yfinance.
    import asyncio
    from utils.market_data import FetcheurDonnees
    md = await asyncio.to_thread(FetcheurDonnees.obtenir_donnees, symbole, timeframe)
    if not md:
        raise HTTPException(404, f"Données non disponibles pour {symbole}")
    return md.to_dict()


@app.get("/api/symboles")
async def symboles_disponibles():
    from config import (SYMBOLES_CRYPTO, SYMBOLES_ACTIONS, SYMBOLES_FOREX,
                        SYMBOLES_INDICES, SYMBOLES_MATIERES)
    return {
        "crypto": SYMBOLES_CRYPTO,
        "actions": SYMBOLES_ACTIONS,
        "forex": SYMBOLES_FOREX,
        "indices": SYMBOLES_INDICES,
        "matieres": SYMBOLES_MATIERES,
        "defaut": SYMBOLES_CRYPTO[:3] + SYMBOLES_ACTIONS[:3],
    }


# ═══════════════════════════ MT5 ROUTES ════════════════════════════

@app.get("/api/mt5/status")
def mt5_status():
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().get_status()


# ── Installation auto de MetaTrader 5 (Windows, gratuit) ──────────
@app.get("/api/mt5/install/status")
def mt5_install_status():
    from utils.mt5_installer import get_mt5_installer
    return get_mt5_installer().get_status()


@app.post("/api/mt5/install")
async def mt5_install():
    from utils.mt5_installer import get_mt5_installer
    return get_mt5_installer().installer_async()


@app.post("/api/mt5/terminal/fermer")
async def mt5_terminal_fermer():
    """Ferme tous les terminaux MetaTrader 5 (plusieurs = aucun utilisable)."""
    import asyncio
    from utils.mt5_manager import get_mt5_manager
    return await asyncio.to_thread(get_mt5_manager().fermer_terminaux)


@app.post("/api/mt5/terminal/demarrer")
async def mt5_terminal_demarrer():
    """Démarre (ou redémarre) le terminal MetaTrader 5 embarqué.

    Le bouton « Réparer la connexion » de l'interface : c'est ce qu'il faut
    faire quand le terminal a été fermé, ou qu'il ne répond plus. Déporté en
    thread — le premier démarrage d'un terminal neuf peut prendre une minute.
    """
    import asyncio
    from utils.mt5_manager import get_mt5_manager
    gestionnaire = get_mt5_manager()
    if not gestionnaire.terminal_embarque():
        # Pas de terminal livré : lancer celui de la machine, s'il y en a un.
        return await asyncio.to_thread(gestionnaire.launch)
    return await asyncio.to_thread(gestionnaire.demarrer_terminal_embarque, 90)


# ── Multi-comptes : basculer entre plusieurs comptes AvaTrade ──────
@app.get("/api/comptes")
async def comptes_liste():
    from utils import comptes
    return comptes.lister()


@app.post("/api/comptes/basculer")
async def comptes_basculer(body: Dict[str, Any] = Body(...)):
    from utils import comptes
    cid = str(body.get("id", "")).strip()
    if not cid:
        return {"success": False, "error": "Identifiant de compte requis"}
    # Déconnexion + reconnexion complète au terminal : plusieurs secondes.
    import asyncio
    return await asyncio.to_thread(comptes.basculer, cid)


@app.post("/api/comptes/renommer")
async def comptes_renommer(body: Dict[str, Any] = Body(...)):
    from utils import comptes
    cid = str(body.get("id", "")).strip()
    return comptes.renommer(cid, body.get("label", ""))


@app.post("/api/comptes/supprimer")
async def comptes_supprimer(body: Dict[str, Any] = Body(...)):
    from utils import comptes
    cid = str(body.get("id", "")).strip()
    if not cid:
        return {"success": False, "error": "Identifiant de compte requis"}
    return comptes.supprimer(cid)


@app.get("/api/mt5/servers")
async def mt5_servers():
    """Serveurs proposés dans la fenêtre de connexion.

    Trois sources, dans cet ordre d'autorité : ceux que le terminal
    MetaTrader connaît vraiment (lus sur le disque), ceux des comptes déjà
    mémorisés, puis la liste AvaTrade de référence. La fenêtre accepte de
    toute façon un nom SAISI LIBREMENT : aucune liste ne peut couvrir toutes
    les entités du courtier, et un utilisateur dont le serveur manquait
    n'avait auparavant aucun moyen de se connecter.
    """
    from utils.mt5_manager import AVATRADE_SERVERS_LIST, get_mt5_manager
    mt5 = get_mt5_manager()
    # `detectes` = le TERMINAL seul : c'est ce que l'interface annonce comme
    # « détectés sur votre terminal », et c'est ce dont elle pré-remplit le
    # champ. Les comptes mémorisés viennent après, sans usurper ce libellé.
    detectes = mt5.serveurs_terminal()
    connus = mt5.serveurs_connus()
    tous = list(detectes)
    for nom in connus + AVATRADE_SERVERS_LIST:
        if nom not in tous:
            tous.append(nom)
    return {"servers": tous, "detectes": detectes,
            "memorises": [n for n in connus if n not in detectes],
            "reference": AVATRADE_SERVERS_LIST, "saisie_libre": True}


@app.post("/api/mt5/connect")
async def mt5_connect(body: Dict[str, Any] = Body(...)):
    from utils.mt5_manager import get_mt5_manager
    account_raw  = body.get("account", "")
    password     = str(body.get("password", ""))
    account_type = str(body.get("account_type", "demo"))
    server       = str(body.get("server", "")).strip()

    try:
        account = int(str(account_raw).strip())
    except ValueError:
        raise HTTPException(400, "Numéro de compte invalide (chiffres uniquement)")

    if len(str(account)) < 5:
        raise HTTPException(400, "Numéro de compte trop court (min 5 chiffres)")
    if len(password) < 4:
        raise HTTPException(400, "Mot de passe trop court (min 4 caractères)")

    # mt5.initialize() + mt5.login() sont BLOQUANTS (plusieurs secondes, voire
    # dizaines si le terminal démarre) : sans thread, tout le serveur se fige
    # pendant la connexion — y compris l'affichage et le bouton d'arrêt.
    import asyncio
    return await asyncio.to_thread(
        get_mt5_manager().connect, account, password, account_type, server)


@app.post("/api/mt5/attacher")
async def mt5_attacher():
    """Reprend le compte DÉJÀ ouvert dans MetaTrader 5, sans identifiants.

    Le chemin le plus simple : quand le terminal est déjà connecté au compte
    du courtier — le cas normal, MetaTrader mémorise la session — redemander
    numéro, mot de passe et nom EXACT du serveur n'apporte rien et multiplie
    les occasions d'échouer.
    """
    import asyncio
    from utils.mt5_manager import get_mt5_manager
    return await asyncio.to_thread(get_mt5_manager().connecter_terminal_courant)


@app.get("/api/mt5/diagnostic")
def mt5_diagnostic():
    """Ce que l'application voit RÉELLEMENT de MetaTrader 5.

    « IPC timeout » ne dit pas si le terminal est introuvable, injoignable ou
    simplement occupé. Ce relevé donne les faits : version de la librairie,
    installations détectées, terminal joignable, compte ouvert."""
    from utils.mt5_manager import get_mt5_manager, version_librairie
    gest = get_mt5_manager()
    infos: Dict[str, Any] = {
        "librairie": version_librairie(),
        "librairie_disponible": gest._lib_available,
        "installations": gest.find_paths(),
        "serveurs_terminal": gest.serveurs_terminal(),
        "joignable": False,
        "compte_ouvert": None,
        "terminal": {},
    }
    if not gest._lib_available:
        infos["conseil"] = ("La librairie MetaTrader5 n'est pas installée dans "
                            "cette application (trading réel impossible).")
        return infos
    try:
        import MetaTrader5 as mt5
        with gest.verrou:
            # initialize() NU : le seul appel qui se contente de se brancher
            # sur le terminal en cours, sans tenter d'en lancer un autre.
            joignable = bool(mt5.initialize(timeout=10000))
            if joignable:
                compte = mt5.account_info()
                terminal = mt5.terminal_info()
            else:
                infos["derniere_erreur"] = str(mt5.last_error())
                compte = terminal = None
        infos["joignable"] = joignable
        if compte is not None:
            infos["compte_ouvert"] = {
                "login": getattr(compte, "login", None),
                "serveur": getattr(compte, "server", ""),
                "devise": getattr(compte, "currency", ""),
                "societe": getattr(compte, "company", ""),
            }
        if terminal is not None:
            infos["terminal"] = {
                "build": getattr(terminal, "build", None),
                "chemin": getattr(terminal, "path", ""),
                "trading_autorise": bool(getattr(terminal, "trade_allowed", False)),
                "connecte": bool(getattr(terminal, "connected", False)),
            }
    except Exception as e:
        infos["erreur"] = str(e)
    return infos


@app.get("/api/mt5/saved")
async def mt5_saved():
    """Identifiants mémorisés (numéro + serveur, jamais le mot de passe) pour pré-remplir."""
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().identifiants_enregistres()


@app.post("/api/mt5/forget")
async def mt5_forget():
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().oublier_identifiants()


@app.post("/api/mt5/disconnect")
async def mt5_disconnect():
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().disconnect()


@app.post("/api/mt5/launch")
def mt5_launch():
    """POST (et non GET) : lance un processus — un GET mutatif serait
    déclenchable par simple navigation, cookie SameSite=Lax inclus."""
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().launch()


@app.post("/api/mt5/auto-trading")
async def mt5_auto_trading(body: Dict[str, Any] = Body(...)):
    from utils.mt5_manager import get_mt5_manager
    active = bool(body.get("active", False))
    return get_mt5_manager().set_auto_trading(active)


@app.get("/api/mt5/journal")
def mt5_journal(limite: int = 50):
    limite = max(1, min(500, limite))
    from utils.mt5_manager import get_mt5_manager
    return {"journal": get_mt5_manager().get_journal(limite)}


@app.get("/api/mt5/portfolio")
def mt5_portfolio():
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().get_portfolio()


@app.get("/api/mt5/positions")
def mt5_positions():
    from utils.mt5_manager import get_mt5_manager
    return {"positions": get_mt5_manager().get_positions()}


@app.post("/api/positions/fermer-tout")
async def positions_fermer_tout(body: Dict[str, Any] = Body(default={})):
    """Bouton d'urgence de l'accueil : ferme TOUTES les positions ouvertes.

    Sous le VERROU d'exécution de l'auto-trader : sans lui, la boucle pourrait
    ouvrir une position pendant qu'on ferme les autres, et le bouton laisserait
    le compte exposé alors qu'il annonce l'avoir mis à plat.

    `arreter_auto` (optionnel) coupe aussi la boucle de trading — sinon les
    agents rouvrent au cycle suivant, ce qui vide le geste de son sens quand
    l'intention est de tout arrêter.
    """
    import asyncio
    from utils.auto_trader import get_auto_trader
    from utils.mt5_manager import get_mt5_manager

    at = get_auto_trader()
    arreter = bool(body.get("arreter_auto"))

    def _fermer():
        with at._exec_lock:
            return get_mt5_manager().fermer_toutes_positions()

    if arreter:
        at.stop()                       # avant la fermeture : plus aucune ouverture
    resultat = await asyncio.to_thread(_fermer)
    resultat["auto_trader_arrete"] = arreter
    return resultat


@app.post("/api/mt5/trade")
async def mt5_trade(signal: Dict[str, Any] = Body(...)):
    from utils.mt5_manager import get_mt5_manager
    from utils.risk_guard import get_risk_guard
    mt5 = get_mt5_manager()
    # Fermeture explicite : on ferme par ticket. NE PAS router vers
    # execute_trade, qui OUVRE une position sur BUY/SELL sans regarder le
    # drapeau « fermeture » — un ordre de fermeture aurait alors ouvert une
    # nouvelle position tout en contournant les garde-fous.
    if signal.get("fermeture"):
        ticket = signal.get("ticket")
        if not ticket:
            return {"success": False, "error": "Ticket requis pour fermer une position"}
        # thread : aller-retour courtier, ne doit pas bloquer la boucle d'événements
        import asyncio
        return await asyncio.to_thread(mt5.fermer_ticket, ticket)
    # Garde-fous durs sur toute ouverture (ordres manuels comme automatiques),
    # SOUS LE MÊME VERROU que la boucle auto-trader : sinon un ordre manuel et
    # un ordre automatique peuvent s'intercaler entre le contrôle et l'envoi
    # (anti-empilement et plafond de positions contournés).
    from utils.auto_trader import get_auto_trader

    def _executer_verrouille():
        with get_auto_trader()._exec_lock:
            autorise, raison = get_risk_guard().evaluer(mt5.get_portfolio())
            if not autorise:
                return {"success": False,
                        "error": f"Ordre bloqué (sécurité) : {raison}", "bloque": True}
            return mt5.execute_trade(signal)

    # Déporté dans un thread : attendre un verrou (ou un aller-retour courtier)
    # depuis la coroutine gèlerait toute la boucle d'événements du serveur.
    import asyncio
    return await asyncio.to_thread(_executer_verrouille)


@app.get("/api/mt5/trades")
def mt5_trades(limite: int = 100):
    """Historique persistant des ordres exécutés (survit au redémarrage)."""
    limite = max(1, min(1000, limite))
    from utils.database import Database
    db = Database()
    return {"trades": db.lire_ordres(limite), "stats": db.stats_ordres()}


@app.get("/api/trades/export.csv")
def trades_export_csv(limite: int = 10000):
    """Export CSV de l'historique des ordres (téléchargement direct)."""
    limite = max(1, min(100000, limite))
    import csv
    import io
    from fastapi.responses import Response
    from utils.database import Database

    def _safe(v):
        # Anti-injection de formule : Excel/Sheets exécute une cellule commençant
        # par = + - @ (ou tab/CR). On la neutralise par une apostrophe.
        s = "" if v is None else str(v)
        if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
            s = "'" + s
        return s

    rows = Database().lire_ordres(limite)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["horodatage", "symbole", "action", "volume", "prix",
                "ticket", "succes", "mode", "erreur"])
    for r in rows:
        w.writerow([_safe(r.get("horodatage")), _safe(r.get("symbole")),
                    _safe(r.get("action")), r.get("volume"), r.get("prix"),
                    _safe(r.get("ticket")), "oui" if r.get("succes") else "non",
                    _safe(r.get("mode")), _safe(r.get("erreur") or "")])
    # BOM UTF-8 : Excel ouvre alors correctement les accents
    donnees = "﻿" + buf.getvalue()
    return Response(
        content=donnees, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=historique_trades.csv"})


@app.get("/api/performance")
def performance():
    """Vue d'ensemble : portefeuille, ordres, activité des agents + P&L réel."""
    from utils.database import Database
    from utils.mt5_manager import get_mt5_manager
    db = Database()
    chef = get_orchestrateur()
    return {
        "portfolio":       chef.portfolio.to_dict(),
        "reel":            get_mt5_manager().get_historique_reel(30),
        "ordres":          db.stats_ordres(),
        "top_agents":      db.lire_stats_agents()[:10],
        "historique":      list(reversed(db.lire_portfolio_historique(100))),
        "timestamp":       datetime.now().isoformat(),
    }


@app.get("/api/performance/reel")
def performance_reel(jours: int = 30):
    """P&L réel depuis l'historique des deals du compte MT5 connecté."""
    from utils.mt5_manager import get_mt5_manager
    return get_mt5_manager().get_historique_reel(max(1, min(365, jours)))


@app.post("/api/rapport/test")
def rapport_test():
    """Génère et envoie le rapport quotidien immédiatement (test)."""
    from utils.auto_trader import construire_rapport_quotidien
    from utils.notifier import notifier, get_webhook
    jour = datetime.now().strftime("%Y-%m-%d")
    texte = construire_rapport_quotidien(jour)
    if get_webhook():
        notifier(texte)
        return {"success": True, "envoye": True, "rapport": texte}
    return {"success": True, "envoye": False, "rapport": texte,
            "info": "Aucun webhook configuré — rapport non envoyé"}


# ═══════════════════════════════════════ BACKTEST ══════════════════════════

@app.post("/api/backtest")
async def backtest(body: Dict[str, Any] = Body(default={})):
    """Rejoue une stratégie technique sur l'historique d'un symbole."""
    from utils.backtest import lancer_backtest
    symbole = str(body.get("symbole", "BTC-USD")).strip() or "BTC-USD"
    timeframe = str(body.get("timeframe", "1d")).strip() or "1d"
    try:
        capital = float(body.get("capital", 10000))
    except (TypeError, ValueError):
        capital = 10000.0
    capital = max(1.0, capital)   # évite la division par zéro
    # Téléchargement d'historique + rejeu complet : plusieurs secondes.
    import asyncio
    return await asyncio.to_thread(lancer_backtest, symbole, timeframe, capital)


# ═══════════════════════════════════════ NOTIFICATIONS ═════════════════════

@app.get("/api/notifications/config")
async def notif_config():
    from utils.notifier import get_webhook, get_ntfy
    url = get_webhook()
    ntfy = get_ntfy()
    # Ne pas exposer les valeurs complètes : le webhook comme le sujet ntfy
    # sont des secrets (quiconque les connaît peut publier / s'abonner).
    apercu_ntfy = ""
    if ntfy:
        apercu_ntfy = ntfy if len(ntfy) <= 6 else (ntfy[:3] + "…" + ntfy[-2:])
    return {"configure": bool(url), "apercu": (url[:32] + "…") if url else "",
            "ntfy_configure": bool(ntfy), "ntfy_apercu": apercu_ntfy}


@app.post("/api/notifications/config")
async def notif_set(body: Dict[str, Any] = Body(...)):
    from utils.notifier import set_webhook, set_ntfy
    ok = True
    if "webhook_url" in body:
        ok = set_webhook(body.get("webhook_url", "")) and ok
    if "ntfy_topic" in body:
        ok = set_ntfy(body.get("ntfy_topic", "")) and ok
    return {"success": ok}


@app.post("/api/notifications/test")
async def notif_test():
    # Jusqu'à deux requêtes HTTP synchrones (8 s de délai chacune) : sans
    # thread, le serveur reste figé ~16 s si les canaux ne répondent pas.
    import asyncio
    from utils.notifier import tester
    return await asyncio.to_thread(tester)


# ═══════════════════════════════════════ RISK GUARD ════════════════════════

@app.get("/api/risk/status")
async def risk_status():
    from utils.risk_guard import get_risk_guard
    return get_risk_guard().get_status()


@app.post("/api/risk/config")
async def risk_config(body: Dict[str, Any] = Body(...)):
    from utils.risk_guard import get_risk_guard
    return get_risk_guard().configurer(body)


# ═══════════════════════════ MOTEUR DE PERFORMANCE ═════════════════════════

@app.get("/api/trading/config")
async def trading_config_get():
    """Réglages de performance : levier, risque par ordre, objectif du jour."""
    from utils import trading_config
    return {"config": trading_config.tout()}


@app.post("/api/trading/config")
async def trading_config_set(body: Dict[str, Any] = Body(...)):
    """Applique les réglages saisis dans l'interface (tout ou rien)."""
    from utils import trading_config
    return trading_config.configurer(body)


@app.post("/api/trading/levier")
async def trading_levier(body: Dict[str, Any] = Body(...)):
    """Règle le levier courant (1 → levier max). Utilisé par l'interface ET
    par les agents IA, qui l'ajustent à chaque décision selon leur conviction
    et la volatilité du marché."""
    from utils import trading_config
    valeur = body.get("levier")
    if valeur is None:
        return {"success": False, "error": "Champ « levier » requis",
                "levier": trading_config.levier_actuel()}
    try:
        demande = float(str(valeur).replace(",", "."))
    except (TypeError, ValueError):
        return {"success": False, "error": "Levier invalide (nombre attendu)",
                "levier": trading_config.levier_actuel()}
    # Depuis l'interface, le réglage doit TENIR : on l'enregistre plutôt que
    # de le laisser écraser au prochain cycle par la décision des agents.
    if body.get("source") == "interface":
        return trading_config.configurer({"levier": demande})
    return {"success": True,
            "levier": trading_config.ajuster_levier(demande, source="agents IA")}


@app.get("/api/objectif")
def objectif_jour():
    """Progression du jour vers l'objectif de gain (réalisé + latent)."""
    from utils.auto_trader import objectif_du_jour
    return objectif_du_jour()


@app.post("/api/preflight")
async def preflight(body: Dict[str, Any] = Body(default={})):
    """Vérification AVANT trading réel : rejoue le chemin de décision complet
    sur le compte connecté et rapporte ce qui SERAIT envoyé. N'envoie AUCUN
    ordre. Déporté dans un thread (accès courtier + réseau bloquants)."""
    import asyncio
    from utils.preflight import verifier
    # Même normalisation que les deux autres points d'entrée : sans elle,
    # « BAC » était vérifié comme trois symboles B, A et C — et un nombre
    # levait un TypeError rendu en 500.
    symboles = normaliser_symboles(body.get("symboles")) or None
    return await asyncio.to_thread(verifier, symboles)


@app.post("/api/risk/reset")
async def risk_reset():
    from utils.risk_guard import get_risk_guard
    from utils.auto_trader import get_auto_trader
    r = get_risk_guard().reset_kill()
    # Réarmer le kill-switch lève aussi la suspension de l'auto-trader,
    # sinon la boucle resterait indéfiniment en surveillance seule.
    at = get_auto_trader()
    at._trading_suspendu = False
    at._raison_suspension = ""
    return r


# ═══════════════════════════════════════ AUTO-TRADER ═══════════════════════

@app.get("/api/auto-trader/status")
def auto_trader_status():
    from utils.auto_trader import get_auto_trader
    return get_auto_trader().get_status()


@app.post("/api/auto-trader/start")
async def auto_trader_start(body: Dict[str, Any] = Body(...)):
    from utils.auto_trader import get_auto_trader
    from config import SYMBOLES_DEFAULT
    # Normalisation STRICTE, partagée avec /api/analyser et /api/preflight
    # (cf. normaliser_symboles : une chaîne vaut UN symbole, jamais une suite
    # de caractères — sinon « BAC » devenait B, A et C, trois VRAIS tickers
    # sur lesquels de vrais ordres pouvaient partir).
    symboles = normaliser_symboles(body.get("symboles")) or list(SYMBOLES_DEFAULT[:3])
    try:
        interval = int(body.get("interval", 60))
    except (TypeError, ValueError):
        interval = 60                       # évite un 500 sur {"interval":"abc"}
    return get_auto_trader().start(symboles, interval)


def _lire_config_fichier() -> Dict[str, Any]:
    # Point d'accès unique et verrouillé (évite d'écraser les clés des
    # autres modules : mot de passe, secret de session, webhook…)
    from utils import app_config
    return app_config.tout()


def _ecrire_config_fichier(cfg: Dict[str, Any]):
    from utils import app_config
    app_config.update(**cfg)


def _ollama_installe() -> Dict[str, Any]:
    """Ollama est-il présent sur la machine (embarqué OU installé par
    l'utilisateur) ? Sert à distinguer « absent » de « installé mais arrêté » :
    les deux affichaient « Ollama non détecté », et l'écran de configuration
    proposait un téléchargement de ~3 Go à qui avait déjà tout installé."""
    try:
        from utils.ollama_embedded import get_ollama_embedded
        emb = get_ollama_embedded()
        chemin = emb.binaire()
        livre = emb.ollama_livre_avec_lapp() is not None
        return {"installe": chemin is not None,
                "chemin": str(chemin) if chemin else "",
                # « embarqué » = livré DANS l'exe au build : l'interface le
                # signale pour qu'on cesse de proposer un téléchargement de
                # 3 Go à qui a déjà tout dans son application.
                "embarque": livre,
                "origine": ("livré avec l'application" if livre
                            else "application" if emb.est_installe() else "système")}
    except Exception:
        return {"installe": False, "chemin": "", "origine": "", "embarque": False}


@app.get("/api/ollama/status")
def ollama_status():
    # def (non-async) : FastAPI l'exécute dans un thread — l'appel réseau
    # bloquant ne fige pas l'event loop.
    from config import OLLAMA_URL, ollama_model_actif, use_ollama_actif
    import urllib.request
    modele = ollama_model_actif()
    presence = _ollama_installe()
    try:
        # ProxyHandler({}) : ignore le proxy système Windows pour localhost
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        models = [m["name"] for m in data.get("models", [])]
        has_model = any(modele.split(":")[0] in m for m in models)
        return {"available": True, "models": models,
                "configured_model": modele, "model_ready": has_model,
                "use_ollama": use_ollama_actif(), "url": OLLAMA_URL, **presence}
    except Exception as e:
        return {"available": False, "error": str(e),
                "configured_model": modele, "use_ollama": use_ollama_actif(),
                **presence}


@app.post("/api/ollama/demarrer")
async def ollama_demarrer():
    """Démarre un Ollama DÉJÀ installé (embarqué ou système) sans rien
    retélécharger. Déporté en thread : le lancement attend que le serveur
    réponde, jusqu'à une trentaine de secondes."""
    import asyncio
    from utils.ollama_embedded import get_ollama_embedded
    emb = get_ollama_embedded()
    if emb.binaire() is None:
        return {"success": False,
                "error": "Ollama n'est installé ni dans l'application ni sur la machine."}
    ok = await asyncio.to_thread(emb.demarrer, 30)
    return {"success": bool(ok),
            "error": "" if ok else (emb._erreur_demarrage or "Démarrage impossible")}


@app.get("/api/ollama/embedded/status")
def ollama_embedded_status():
    from utils.ollama_embedded import get_ollama_embedded
    return get_ollama_embedded().get_status()


@app.post("/api/ollama/embedded/install")
async def ollama_embedded_install(body: Dict[str, Any] = Body(default={})):
    """Installation automatique : télécharge Ollama + le modèle, tout géré par l'app."""
    from utils.ollama_embedded import get_ollama_embedded
    # Modèle non précisé → installer_async prend OLLAMA_MODEL (le modèle des
    # agents), pour ne jamais installer un modèle différent de celui utilisé.
    modele = str(body.get("model", "")).strip() or None
    force = bool(body.get("force", False))
    return get_ollama_embedded().installer_async(modele, force=force)


# ═══════════════════════════ DIAGNOSTIC ════════════════════════════════════

@app.get("/api/diagnostic")
def diagnostic():
    """Tout ce qu'il faut pour comprendre une panne, en un seul appel.

    Route AUTHENTIFIÉE (le middleware protège /api/*) : elle expose des
    chemins et des traces, réservés au propriétaire de l'application.

    Elle existe parce que l'exe Windows n'a pas de console : sans elle, une
    erreur de route ne laissait qu'un bandeau rouge sans cause, et la trace
    n'était visible nulle part.
    """
    import platform
    infos: Dict[str, Any] = {
        "version": APP_VERSION,
        "python": sys.version.split()[0],
        "systeme": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "compile": bool(getattr(sys, "frozen", False)),
        "racine": _ROOT,
        "erreurs": list(_ERREURS_RECENTES or []),
    }

    # Chaque bloc est isolé : c'est justement quand un sous-système est cassé
    # qu'on consulte cette page — elle ne doit pas tomber avec lui.
    def _bloc(nom, fn):
        try:
            infos[nom] = fn()
        except Exception as e:
            infos[nom] = {"erreur": f"{type(e).__name__}: {e}"}

    def _ia():
        from utils import moteur_ia
        return moteur_ia.statut()

    def _mt5():
        """Tout ce qui explique un problème de connexion MetaTrader.

        La version précédente filtrait `get_status()` sur des noms de champs
        qui n'existent pas — elle renvoyait donc un objet VIDE, c'est-à-dire
        rien d'utile au moment précis où l'on vient chercher de l'aide. On
        compose désormais le diagnostic à partir des vraies sources.
        """
        from utils.mt5_installer import MT5Installer
        from utils.mt5_manager import get_mt5_manager, version_librairie
        gestionnaire = get_mt5_manager()
        etat = gestionnaire.get_status()
        compte = etat.get("account_info") or {}
        portable = gestionnaire.terminal_embarque()
        return {
            "librairie": version_librairie(),
            "librairie_disponible": gestionnaire._lib_available,
            "terminal_portable_embarque": portable or "",
            # « avatrade » attendu : un terminal générique ne connaît aucun
            # serveur du courtier et refusera la connexion au compte.
            "courtier_embarque": MT5Installer.courtier_embarque(),
            "serveurs_embarques": MT5Installer.serveurs_embarques()[:8],
            "installeur_embarque": str(MT5Installer.setup_embarque() or ""),
            "terminaux_detectes": gestionnaire.find_paths(),
            # Nombre de terminaux RÉELLEMENT ouverts : au-delà de 1, aucun
            # n'est utilisable (un seul peut réserver le port de communication).
            "terminaux_ouverts": gestionnaire.terminaux_en_cours(),
            "terminal_repond": gestionnaire._terminal_repond(timeout_ms=3000),
            "connecte": bool(etat.get("connected")),
            # Identifiants JAMAIS exposés : seulement de quoi reconnaître le
            # compte (numéro et serveur figurent déjà à l'écran).
            "compte": compte.get("account", ""),
            "serveur": compte.get("server", ""),
            "type": compte.get("account_type", ""),
            "simule": compte.get("simulated"),
            "trading_auto": etat.get("auto_trading"),
            "algotrading": gestionnaire.algotrading_actif(),
            "operation_en_cours": etat.get("operation", ""),
            "identifiants_memorises":
                gestionnaire.identifiants_enregistres().get("enregistres", False),
            "serveurs_connus": gestionnaire.serveurs_connus()[:8],
        }

    def _base():
        from config import DB_PATH
        return {"chemin": str(DB_PATH), "existe": os.path.exists(DB_PATH),
                "taille_ko": (round(os.path.getsize(DB_PATH) / 1024)
                              if os.path.exists(DB_PATH) else 0)}

    def _agents():
        from agents import TOUS_LES_AGENTS
        return {"charges": len(TOUS_LES_AGENTS)}

    def _installation():
        from utils import mise_a_jour
        return {"dossier": mise_a_jour.chemin_installation(),
                "verification_maj": bool(mise_a_jour.url_configuree())}

    def _empaquetage():
        """Empaquetage MSIX : lecture seule du dossier d'installation.

        Sans cette information, un utilisateur installé depuis le Microsoft
        Store et un utilisateur installé par l'installeur classique
        rapporteraient exactement le même diagnostic — alors que le premier
        ne peut pas se servir d'un terminal MetaTrader portable embarqué.
        """
        from utils import msix
        return msix.resume()

    _bloc("ia", _ia)
    _bloc("mt5", _mt5)
    _bloc("base", _base)
    _bloc("agents", _agents)
    _bloc("installation", _installation)
    _bloc("empaquetage", _empaquetage)
    return infos


@app.get("/api/mise-a-jour")
async def mise_a_jour_disponible():
    """Une version plus récente est-elle publiée ?

    Route AUTHENTIFIÉE (le middleware protège /api/*). Elle ne télécharge et
    n'installe RIEN : elle compare la version installée à celle annoncée par
    le manifeste `AGENCE_UPDATE_URL`. Sans cette variable, elle répond
    « désactivé » sans ouvrir la moindre connexion — l'application reste
    100 % locale tant que son propriétaire n'en décide pas autrement.
    """
    from utils import mise_a_jour
    return mise_a_jour.verifier()


@app.post("/api/diagnostic/purger")
async def diagnostic_purger():
    """Vide le journal des erreurs (après avoir corrigé un problème)."""
    if _ERREURS_RECENTES is not None:
        _ERREURS_RECENTES.clear()
    return {"success": True}


# ═══════════════════════ MOTEUR IA (Ollama / Hermès) ═══════════════════════
# Un seul endroit pour choisir et surveiller le moteur qui répond aux 46
# assistants et au Chef d'Orchestre. Les deux tournent SUR LA MACHINE : il
# n'existe pas de mode distant, donc pas de clé API à saisir.

@app.get("/api/ia/statut")
def ia_statut():
    """État des trois moteurs + ce qui est réellement embarqué dans cet exe."""
    from utils import moteur_ia
    return moteur_ia.statut()


@app.post("/api/ia/moteur")
async def ia_choisir_moteur(body: Dict[str, Any] = Body(...)):
    """Sélectionne le moteur IA (Ollama ou Hermès) et le démarre.

    Le démarrage est déporté en thread : charger un modèle de plusieurs
    gigaoctets prend jusqu'à deux minutes, l'interface ne doit pas attendre.
    """
    import asyncio
    from utils import moteur_ia
    nom = str(body.get("moteur", "")).strip().lower()

    # Modèle éventuellement choisi EN MÊME TEMPS que le moteur : l'appliquer
    # avant le démarrage, sinon le serveur se lance sur l'ancien modèle.
    modele = str(body.get("modele", "")).strip()
    if modele:
        cfg_cle = {"ollama": "OLLAMA_MODEL", "hermes": "HERMES_MODEL"}.get(nom)
        if cfg_cle:
            os.environ[cfg_cle] = modele
            _ecrire_config_fichier({cfg_cle: modele})

    resultat = moteur_ia.definir(nom)
    if not resultat.get("success"):
        return resultat
    asyncio.get_running_loop().run_in_executor(
        None, moteur_ia.demarrer_si_possible, nom)
    return {**resultat, "modele": modele}


# ── Hermès (llama.cpp + modèle NousResearch) ──────────────────────
@app.get("/api/hermes/status")
def hermes_status():
    from utils.hermes_embedded import get_hermes_embedded
    return get_hermes_embedded().get_status()


@app.post("/api/hermes/demarrer")
async def hermes_demarrer():
    """Démarre Hermès s'il est déjà présent (embarqué ou installé)."""
    import asyncio
    from utils.hermes_embedded import get_hermes_embedded
    emb = get_hermes_embedded()
    if not emb.est_installe():
        return {"success": False,
                "error": "Hermès n'est ni embarqué dans l'application ni installé."}
    ok = await asyncio.to_thread(emb.demarrer, 120)
    return {"success": bool(ok),
            "error": "" if ok else (emb._erreur_demarrage or "Démarrage impossible")}


@app.post("/api/hermes/install")
async def hermes_install(body: Dict[str, Any] = Body(default={})):
    """Installation d'Hermès — inutile quand l'exe l'embarque déjà."""
    from utils.hermes_embedded import get_hermes_embedded
    modele = str(body.get("model", "")).strip() or None
    force = bool(body.get("force", False))
    return get_hermes_embedded().installer_async(modele, force=force)


@app.post("/api/hermes/configure")
async def hermes_configure(body: Dict[str, Any] = Body(...)):
    """Choisit le modèle Hermès et bascule le moteur sur Hermès."""
    from utils.hermes_embedded import MODELES, MODELE_DEFAUT
    from utils import moteur_ia
    modele = str(body.get("model", "")).strip()
    if modele not in MODELES:
        modele = MODELE_DEFAUT
    os.environ["HERMES_MODEL"] = modele
    _ecrire_config_fichier({"HERMES_MODEL": modele})
    resultat = moteur_ia.definir(moteur_ia.HERMES)
    return {**resultat, "model": modele}


@app.post("/api/ollama/configure")
async def ollama_configure(body: Dict[str, Any] = Body(...)):
    model = str(body.get("model", "llama3.2")).strip()
    os.environ["OLLAMA_MODEL"] = model
    _ecrire_config_fichier({"OLLAMA_MODEL": model})
    # MOTEUR_IA : sans lui, choisir Ollama depuis l'écran de configuration
    # laissait le moteur précédent (Hermès) actif — « USE_OLLAMA » ne
    # distingue pas les deux moteurs locaux.
    from utils import moteur_ia
    moteur_ia.definir(moteur_ia.OLLAMA)
    return {"success": True, "model": model}


@app.post("/api/auto-trader/stop")
async def auto_trader_stop():
    from utils.auto_trader import get_auto_trader
    return get_auto_trader().stop()


# ═══════════════════════════════════════ SETUP (premier lancement) ══════════

_SETUP_CONFIG = os.path.join(os.path.expanduser("~"), ".agence_financiere", "config.json")

_SETUP_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Configuration — Agence Numérique Financière</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0c10;color:#dde3ec;font-family:system-ui,-apple-system,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}
.wrap{max-width:520px;width:100%}
.logo{font-size:36px;text-align:center;margin-bottom:10px}
h1{color:#64b5f6;font-size:21px;font-weight:700;text-align:center;margin-bottom:4px}
.sub{color:#7a8aaa;text-align:center;margin-bottom:28px;font-size:13px}
.tabs{display:flex;gap:8px;margin-bottom:20px}
.tab{flex:1;padding:10px 6px;background:#131929;border:1.5px solid #1e2a45;border-radius:10px;
     color:#7a8aaa;font-size:12.5px;font-weight:600;cursor:pointer;text-align:center;transition:.2s}
.tab small{font-size:10.5px;font-weight:500;opacity:.85}
.badge-embarque{display:inline-block;background:#0d2a12;border:1px solid #1f5a2a;color:#66bb6a;
     border-radius:20px;padding:3px 10px;font-size:11px;font-weight:600;margin-bottom:12px}
.tab.active{background:#0d1a3a;border-color:#64b5f6;color:#64b5f6}
.panel{background:#131929;border:1px solid #1e2a45;border-radius:16px;padding:28px}
.panel-inner{display:none}.panel-inner.active{display:block}
label{display:block;font-size:12px;color:#9aadcc;margin-bottom:7px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
input,select{width:100%;padding:12px 14px;background:#151a23;border:1px solid #232a37;
      border-radius:8px;color:#e0e0e0;font-size:13px;transition:.2s;font-family:monospace;margin-bottom:16px}
input:focus,select:focus{outline:none;border-color:#64b5f6}
.btn{width:100%;padding:13px;background:#5b8def;
     color:#fff;border:none;border-radius:9px;font-size:14px;font-weight:600;
     cursor:pointer;transition:.2s}
.btn:hover{background:linear-gradient(135deg,#1976d2,#1565c0)}
.btn-green{background:linear-gradient(135deg,#1b5e20,#2e7d32)}
.btn-green:hover{background:linear-gradient(135deg,#2e7d32,#388e3c)}
.hint{font-size:11px;color:#4a5a7a;margin-top:12px;text-align:center}
.hint a{color:#64b5f6;text-decoration:none}
.err{background:#1a0a0a;border:1px solid #5a1a1a;color:#ff6b6b;
     padding:10px;border-radius:7px;margin-top:12px;font-size:12px;display:none}
.ok{background:#0a1a0a;border:1px solid #1a5a1a;color:#66bb6a;
    padding:10px;border-radius:7px;margin-top:12px;font-size:12px;display:none}
.status-row{display:flex;align-items:center;gap:10px;padding:10px 14px;
            background:#0d1220;border-radius:8px;margin-bottom:16px;font-size:13px}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot.green{background:#4caf50}.dot.red{background:#f44336}.dot.orange{background:#ff9800}
.steps{padding:0;list-style:none;counter-reset:s}
.steps li{counter-increment:s;padding:8px 0 8px 32px;position:relative;color:#9aadcc;font-size:13px;border-bottom:1px solid #1a2040}
.steps li:last-child{border:none}
.steps li::before{content:counter(s);position:absolute;left:0;width:22px;height:22px;
  background:#1565c0;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:700;color:#fff}
code{background:#0d1220;padding:2px 6px;border-radius:4px;font-family:monospace;color:#64b5f6;font-size:12px}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">📊</div>
  <h1>Agence Numérique Financière</h1>
  <p class="sub">46 agents IA de trading — 100 % sur votre machine, sans clé API</p>
  <p class="sub" id="mode-actuel" style="color:#64b5f6;font-weight:600;margin-top:-20px"></p>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('local')" id="tab-local">🖥️ Ollama<br><small>Local, gratuit</small></div>
    <div class="tab" onclick="switchTab('hermes')" id="tab-hermes">🪽 Hermès<br><small>Local, gratuit</small></div>
  </div>

  <div class="panel">
    <!-- PANNEAU LOCAL -->
    <div class="panel-inner active" id="panel-local">
      <div class="status-row" id="ollama-status-row">
        <div class="dot orange" id="ollama-dot"></div>
        <span id="ollama-status-txt">Vérification Ollama...</span>
      </div>
      <div id="ollama-embarque-badge" class="badge-embarque" style="display:none">
        ✅ Livré avec l'application — rien à installer
      </div>
      <label>Modèle IA local</label>
      <select id="ollama-model">
        <option value="llama3.2">llama3.2 (recommandé — rapide)</option>
        <option value="llama3.1:8b">llama3.1:8b (meilleure qualité)</option>
        <option value="mistral">mistral:7b (bon en français)</option>
        <option value="phi4-mini">phi4-mini (très rapide, léger)</option>
        <option value="gemma2:9b">gemma2:9b (haute qualité)</option>
        <option value="deepseek-r1:7b">deepseek-r1:7b (raisonnement)</option>
      </select>
      <div id="ollama-ready-section" style="display:none">
        <button class="btn btn-green" onclick="saveOllama()">✅ Démarrer avec l'IA locale →</button>
      </div>
      <div id="ollama-install-section">
        <p style="color:#9aadcc;font-size:13px;margin-bottom:14px">
          IA locale non détectée. L'application peut tout installer elle-même :
          Ollama + le modèle choisi ci-dessus (~3 Go, une seule fois).
        </p>
        <button class="btn btn-green" id="btn-install-auto" onclick="installerAuto()">
          📥 Installation automatique
        </button>
        <p class="hint" id="reinstall-link" style="display:none;margin-top:8px">
          Échec répété ? <a href="#" onclick="installerAuto(true);return false">Réinstallation
          complète (re-téléchargement)</a>
        </p>
        <div id="install-progress" style="display:none;margin-top:14px">
          <div style="background:#0d1220;border-radius:8px;height:10px;overflow:hidden">
            <div id="install-bar" style="background:linear-gradient(90deg,#1565c0,#64b5f6);
                 height:100%;width:0%;transition:width .5s"></div>
          </div>
          <p id="install-detail" style="font-size:12px;color:#9aadcc;margin-top:8px">Préparation...</p>
        </div>
        <details style="margin-top:14px">
          <summary style="font-size:12px;color:#4a5a7a;cursor:pointer">Installation manuelle (avancé)</summary>
          <ol class="steps" style="margin-top:8px">
            <li>Téléchargez <a href="https://ollama.com/download" target="_blank" style="color:#64b5f6">ollama.com/download</a></li>
            <li>Terminal : <code>ollama pull llama3.2</code></li>
            <li>Cliquez « Vérifier à nouveau »</li>
          </ol>
        </details>
        <button class="btn" style="margin-top:14px" onclick="checkOllama()">🔄 Vérifier à nouveau</button>
        <p class="hint" style="margin-top:10px">
          <a href="#" onclick="saveOllama();return false">Ollama déjà installé ? Continuer quand même →</a>
        </p>
      </div>
      <div class="err" id="err-local"></div>
    </div>

    <!-- PANNEAU HERMÈS -->
    <div class="panel-inner" id="panel-hermes">
      <div class="status-row" id="hermes-status-row">
        <div class="dot orange" id="hermes-dot"></div>
        <span id="hermes-status-txt">Vérification d'Hermès...</span>
      </div>
      <div id="hermes-embarque-badge" class="badge-embarque" style="display:none">
        ✅ Livré avec l'application — rien à installer
      </div>
      <label>Modèle Hermès</label>
      <select id="hermes-model"></select>
      <div id="hermes-ready-section" style="display:none">
        <button class="btn btn-green" onclick="saveHermes()">✅ Démarrer avec Hermès →</button>
      </div>
      <div id="hermes-install-section" style="display:none">
        <p style="color:#9aadcc;font-size:13px;margin-bottom:14px">
          Hermès n'est pas embarqué dans cette copie de l'application.
          Elle peut le télécharger elle-même : moteur llama.cpp + le modèle
          choisi ci-dessus (une seule fois).
        </p>
        <button class="btn btn-green" id="btn-hermes-install" onclick="installerHermes()">
          📥 Installation automatique
        </button>
        <div id="hermes-progress" style="display:none;margin-top:14px">
          <div style="background:#0d1220;border-radius:8px;height:10px;overflow:hidden">
            <div id="hermes-bar" style="background:linear-gradient(90deg,#1565c0,#64b5f6);
                 height:100%;width:0%;transition:width .5s"></div>
          </div>
          <p id="hermes-detail" style="font-size:12px;color:#9aadcc;margin-top:8px">Préparation...</p>
        </div>
        <button class="btn" style="margin-top:14px" onclick="checkHermes()">🔄 Vérifier à nouveau</button>
      </div>
      <div class="err" id="err-hermes"></div>
      <p class="hint" style="margin-top:12px">Hermès (NousResearch) suit les consignes
      de format à la lettre — utile pour les verdicts courts que l'application demande
      à ses 46 assistants.</p>
    </div>
  </div>

  <!-- iPhone / PWA (rempli par loadState si accessible en Wi-Fi) -->
  <div id="iphone-hint" style="display:none;margin-top:16px;background:#0d1a2e;border:1px solid #1e3a5f;
       border-radius:12px;padding:14px 16px;font-size:12px;color:#9aadcc;line-height:1.6">
    📱 <b style="color:#64b5f6">Sur votre iPhone</b> (même Wi-Fi) — ouvrez dans Safari :
    <div style="font-family:monospace;color:#64b5f6;font-size:14px;margin:6px 0" id="iphone-url"></div>
    puis <b>Partager</b> → <b>« Sur l'écran d'accueil »</b> : l'app s'installe avec son icône,
    comme une vraie application.
  </div>
</div>
<script>
const ONGLETS=['local','hermes'];
function switchTab(t){
  if(!ONGLETS.includes(t))t='local';
  ONGLETS.forEach(o=>{
    document.getElementById('tab-'+o).classList.toggle('active',o===t);
    document.getElementById('panel-'+o).classList.toggle('active',o===t);
  });
  if(t==='hermes')checkHermes();
}

// ── Hermès ────────────────────────────────────────────────────────
let _hermesModelesCharges=false;
async function checkHermes(){
  const txt=document.getElementById('hermes-status-txt');
  const dot=document.getElementById('hermes-dot');
  txt.textContent='Vérification...';dot.className='dot orange';
  try{
    const d=await(await fetch('/api/hermes/status')).json();
    // Liste des modèles : renseignée par le serveur (une seule source de
    // vérité, la page ne peut plus proposer un modèle inconnu du moteur).
    if(!_hermesModelesCharges&&Array.isArray(d.modeles)){
      const sel=document.getElementById('hermes-model');
      sel.innerHTML='';
      d.modeles.forEach(m=>{
        const o=document.createElement('option');
        o.value=m.cle;o.textContent=m.libelle;
        if(m.cle===d.modele_cle)o.selected=true;
        sel.appendChild(o);
      });
      _hermesModelesCharges=true;
    }
    document.getElementById('hermes-embarque-badge').style.display=d.embarque?'inline-block':'none';
    const pret=document.getElementById('hermes-ready-section');
    const inst=document.getElementById('hermes-install-section');
    if(d.serveur_actif){
      dot.className='dot green';
      txt.textContent='Hermès actif — modèle '+(d.modele_cle||'');
      pret.style.display='block';inst.style.display='none';
    }else if(d.installe){
      dot.className='dot orange';
      txt.textContent=d.embarque?'Hermès embarqué — prêt à démarrer'
                                :'Hermès installé — prêt à démarrer';
      pret.style.display='block';inst.style.display='none';
    }else{
      dot.className='dot red';
      txt.textContent='Hermès non détecté';
      pret.style.display='none';inst.style.display='block';
    }
    if(['telechargement','extraction','modele','demarrage'].includes(d.etape)){
      document.getElementById('hermes-progress').style.display='block';
      _pollHermes();
    }
  }catch(e){
    dot.className='dot red';txt.textContent='Erreur de connexion';
  }
}

async function saveHermes(){
  const model=document.getElementById('hermes-model').value;
  const btn=document.querySelector('#hermes-ready-section .btn-green');
  btn.textContent='Démarrage...';btn.disabled=true;
  const err=document.getElementById('err-hermes');err.style.display='none';
  try{
    const d=await(await fetch('/api/hermes/configure',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({model})})).json();
    if(d.success){
      // Le serveur charge le modèle en arrière-plan : on lance le démarrage
      // puis on entre dans le tableau de bord, qui affichera l'état réel.
      fetch('/api/hermes/demarrer',{method:'POST'});
      window.location.href='/';return;
    }
    err.textContent=d.error||'Erreur de configuration';err.style.display='block';
  }catch(e){err.textContent='Erreur réseau';err.style.display='block';}
  btn.disabled=false;btn.textContent='✅ Démarrer avec Hermès →';
}

async function installerHermes(){
  const model=document.getElementById('hermes-model').value;
  const btn=document.getElementById('btn-hermes-install');
  btn.disabled=true;btn.textContent='Installation en cours...';
  document.getElementById('hermes-progress').style.display='block';
  try{
    await fetch('/api/hermes/install',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({model})});
  }catch(e){}
  _pollHermes();
}

let _hermesTimer=null;
function _pollHermes(){
  if(_hermesTimer)return;                 // un seul suivi à la fois
  _hermesTimer=setInterval(async()=>{
    try{
      const s=await(await fetch('/api/hermes/status')).json();
      document.getElementById('hermes-bar').style.width=(s.progression||0)+'%';
      document.getElementById('hermes-detail').textContent=
        (s.detail||s.etape)+(s.progression?(' — '+Math.round(s.progression)+'%'):'');
      if(s.etape==='pret'){
        clearInterval(_hermesTimer);_hermesTimer=null;
        document.getElementById('hermes-detail').textContent='Hermès prêt !';
        checkHermes();
      }else if(s.etape==='erreur'){
        clearInterval(_hermesTimer);_hermesTimer=null;
        document.getElementById('hermes-detail').textContent=
          'Erreur : '+(s.erreur||'installation impossible')
          +(s.journal?(' — journal : '+s.journal):'');
        const b=document.getElementById('btn-hermes-install');
        b.disabled=false;b.textContent='📥 Réessayer';
      }
    }catch(e){}
  },1500);
}

async function checkOllama(){
  document.getElementById('ollama-status-txt').textContent='Vérification...';
  document.getElementById('ollama-dot').className='dot orange';
  try{
    const r=await fetch('/api/ollama/status');
    const d=await r.json();
    document.getElementById('ollama-embarque-badge').style.display=d.embarque?'inline-block':'none';
    if(d.available){
      document.getElementById('ollama-dot').className='dot green';
      if(d.model_ready){
        document.getElementById('ollama-status-txt').textContent=
          'Ollama prêt — modèle '+d.configured_model+' disponible';
      } else {
        document.getElementById('ollama-status-txt').textContent=
          'Ollama actif — téléchargez le modèle avec : ollama pull '+d.configured_model;
      }
      document.getElementById('ollama-ready-section').style.display='block';
      document.getElementById('ollama-install-section').style.display='none';
    } else if(d.installe){
      // Installé mais ARRÊTÉ : ne surtout pas proposer un téléchargement de
      // ~3 Go à quelqu'un qui a déjà tout ce qu'il faut — il suffit de lancer
      // le serveur. C'est le cas de tous ceux qui ont installé Ollama
      // eux-mêmes depuis ollama.com.
      document.getElementById('ollama-dot').className='dot orange';
      document.getElementById('ollama-status-txt').textContent=
        'Ollama installé ('+(d.origine||'')+') mais pas démarré';
      document.getElementById('ollama-ready-section').style.display='none';
      document.getElementById('ollama-install-section').style.display='block';
      const sec=document.getElementById('ollama-install-section');
      let b=document.getElementById('btn-ollama-start');
      if(!b){
        b=document.createElement('button');
        b.id='btn-ollama-start';b.className='btn btn-green';
        b.style.marginBottom='12px';
        b.onclick=demarrerOllama;
        sec.insertBefore(b,sec.firstChild);
      }
      b.textContent='▶ Démarrer Ollama (déjà installé)';
      b.disabled=false;
    } else {
      document.getElementById('ollama-dot').className='dot red';
      document.getElementById('ollama-status-txt').textContent='Ollama non détecté';
      document.getElementById('ollama-ready-section').style.display='none';
      document.getElementById('ollama-install-section').style.display='block';
      const b=document.getElementById('btn-ollama-start');
      if(b)b.remove();
    }
  }catch(e){
    document.getElementById('ollama-dot').className='dot red';
    document.getElementById('ollama-status-txt').textContent='Erreur de connexion';
  }
}

async function demarrerOllama(){
  const b=document.getElementById('btn-ollama-start');
  if(b){b.disabled=true;b.textContent='Démarrage en cours...';}
  try{
    const r=await fetch('/api/ollama/demarrer',{method:'POST'});
    const d=await r.json();
    if(!d.success){
      const err=document.getElementById('err-local');
      err.textContent='Démarrage impossible : '+(d.error||'');
      err.style.display='block';
    }
  }catch(e){}
  checkOllama();
}

async function saveOllama(){
  const model=document.getElementById('ollama-model').value;
  const btn=document.querySelector('#panel-local .btn-green');
  btn.textContent='Démarrage...';btn.disabled=true;
  try{
    const r=await fetch('/api/ollama/configure',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({model})});
    const d=await r.json();
    if(d.success){window.location.href='/';}
    else{document.getElementById('err-local').textContent='Erreur configuration';
         document.getElementById('err-local').style.display='block';}
  }catch(e){
    document.getElementById('err-local').textContent='Erreur réseau';
    document.getElementById('err-local').style.display='block';
  }
  btn.disabled=false;btn.textContent="✅ Démarrer avec l'IA locale →";
}

async function loadState(){
  try{
    const r=await fetch('/setup/state');
    const s=await r.json();
    const badge=document.getElementById('mode-actuel');
    if(s.moteur==='hermes'){
      badge.textContent='Moteur actuel : Hermès ('+(s.hermes_model||'')+') — local';
      switchTab('hermes');
    }else{
      badge.textContent='Moteur actuel : Ollama ('+s.ollama_model+') — local';
      switchTab('local');
    }
    if(s.lan_url){
      document.getElementById('iphone-url').textContent=s.lan_url;
      document.getElementById('iphone-hint').style.display='block';
    }
  }catch(e){}
  // Reprendre le suivi si une installation est deja en cours
  try{
    const r2=await fetch('/api/ollama/embedded/status');
    const e2=await r2.json();
    if(['telechargement','extraction','demarrage','modele'].includes(e2.etape)){
      document.getElementById('btn-install-auto').disabled=true;
      document.getElementById('install-progress').style.display='block';
      _pollInstall(document.getElementById('ollama-model').value);
    }
  }catch(e){}
}

async function installerAuto(force){
  const model=document.getElementById('ollama-model').value;
  const btn=document.getElementById('btn-install-auto');
  btn.disabled=true;btn.textContent=force?'Réinstallation complète...':'Installation en cours...';
  const lien=document.getElementById('reinstall-link');
  if(lien)lien.style.display='none';
  document.getElementById('install-progress').style.display='block';
  try{
    await fetch('/api/ollama/embedded/install',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model,force:!!force})});
  }catch(e){}
  _pollInstall(model);
}

let _installTimer=null;
function _pollInstall(model){
  if(_installTimer)clearInterval(_installTimer);
  _installTimer=setInterval(async()=>{
    try{
      const r=await fetch('/api/ollama/embedded/status');
      const s=await r.json();
      const bar=document.getElementById('install-bar');
      const det=document.getElementById('install-detail');
      bar.style.width=(s.progression||0)+'%';
      det.textContent=(s.detail||s.etape)+(s.progression?(' — '+Math.round(s.progression)+'%'):'');
      if(s.etape==='pret'){
        clearInterval(_installTimer);
        det.textContent='Installation terminee — IA locale prete !';
        await fetch('/api/ollama/configure',{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({model})});
        checkOllama();
      } else if(s.etape==='erreur'){
        clearInterval(_installTimer);
        det.textContent='Erreur : '+(s.erreur||'installation impossible')
          +(s.journal?(' — journal : '+s.journal):'');
        const btn=document.getElementById('btn-install-auto');
        btn.disabled=false;btn.textContent='📥 Réessayer';
        const lien=document.getElementById('reinstall-link');
        if(lien)lien.style.display='block';
      }
    }catch(e){}
  },1500);
}

loadState();
checkOllama();
</script>
</body>
</html>"""


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return _SETUP_HTML


def _ip_locale() -> str:
    """IP de la machine sur le réseau local (pour accès depuis l'iPhone)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # aucune donnée envoyée
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


@app.get("/setup/state")
async def setup_state(request: Request):
    """Mode IA actuel — la page /setup s'en sert pour pré-sélectionner l'onglet."""
    from config import (ollama_model_actif, hermes_model_actif, moteur_ia_actif)
    cfg = _lire_config_fichier()
    ip = _ip_locale()
    port = request.url.port or 80
    return {
        # Toujours vrai : l'IA de cette application tourne exclusivement sur
        # la machine. Le champ est conservé pour les écrans qui le lisent.
        "use_ollama": True,
        "moteur": moteur_ia_actif(),
        "ollama_model": ollama_model_actif(),
        "hermes_model": hermes_model_actif(),
        "configured": bool(cfg.get("MOTEUR_IA") or cfg.get("USE_OLLAMA")),
        "lan_url": f"http://{ip}:{port}" if ip else "",
    }


@app.post("/setup/mode")
async def setup_mode(body: Dict[str, Any] = Body(...)):
    """Bascule entre les deux moteurs locaux (Ollama, Hermès)."""
    from utils import moteur_ia
    mode = str(body.get("mode", "")).strip()
    resultat = moteur_ia.definir(mode)
    return ({"success": True, "mode": mode} if resultat.get("success")
            else {"success": False, "error": resultat.get("error", "Mode inconnu")})


# ═══════════════════════════════════════════════════════════════════════════

frontend_dir = os.path.join(_ROOT, "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    for _sub in ("css", "js", "icons"):
        _sub_dir = os.path.join(frontend_dir, _sub)
        if os.path.exists(_sub_dir):
            app.mount(f"/{_sub}", StaticFiles(directory=_sub_dir), name=_sub)

    # PWA — manifest et service worker servis à la racine
    @app.get("/manifest.json")
    async def pwa_manifest():
        p = os.path.join(frontend_dir, "manifest.json")
        return FileResponse(p, media_type="application/manifest+json")

    @app.get("/sw.js")
    async def pwa_sw():
        p = os.path.join(frontend_dir, "sw.js")
        return FileResponse(p, media_type="application/javascript")

    # /favicon.ico : demandé automatiquement par les navigateurs et déjà
    # autorisé sans authentification, mais aucune route ne le servait — chaque
    # chargement de page produisait un 404 dans le journal du serveur.
    @app.get("/favicon.ico")
    async def favicon():
        p = os.path.join(frontend_dir, "icons", "favicon.png")
        if not os.path.exists(p):
            raise HTTPException(404, "favicon absent")
        return FileResponse(p, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    from config import BACKEND_HOST, BACKEND_PORT
    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT)
