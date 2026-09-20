"""Authentification par code d'accès — DÉSACTIVÉE PAR DÉFAUT.

L'application ne demande plus aucun code au lancement : c'est un choix
explicite du projet. Tout le mécanisme reste en place et s'active d'un mot.

    APP_AUTH=on            demande un code (497040, ou celui des réglages)
    APP_PASSWORD=<code>    impose un code — implique APP_AUTH=on
    APP_AUTH=off           aucun code (comportement par défaut)

CE QUE CETTE PROTECTION COUVRAIT
--------------------------------
L'application écoute sur `0.0.0.0`, volontairement : c'est ce qui permet de
l'ouvrir depuis un téléphone du même Wi-Fi (voir README, « Sur téléphone »).
Sans code, tout appareil de ce réseau peut donc ouvrir l'interface, lire les
identifiants enregistrés du courtier et déclencher des ordres réels.

Sur un réseau personnel maîtrisé, c'est sans conséquence. Sur un réseau
partagé — café, hôtel, espace de travail, entreprise — cela ne l'est pas.
Deux façons de se protéger, au choix :

    APP_AUTH=on                     rétablir le code d'accès
    BACKEND_HOST=127.0.0.1          n'écouter que sur cette machine
                                    (l'accès téléphone cesse alors de marcher)

Le reste du module — KDF lent et salé, anti-force-brute par IP, cookie signé
HMAC, invalidation des sessions au changement de code — est inchangé et sert
dès que l'authentification est réactivée.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

from utils import app_config

DEFAULT_PASSWORD = "497040"          # changez-le dans les réglages de l'app
COOKIE = "agence_auth"

_secret_cache = None  # secret HMAC gardé en mémoire (évite une lecture disque par requête)
_secret_lock = threading.Lock()

# ── Anti-force-brute : un code à 6 chiffres serait devinable sans limite ──
# Compteurs PAR IP SOURCE : un compteur global permettrait à n'importe qui sur
# le réseau de verrouiller l'accès du propriétaire légitime (déni de service)
# en épuisant volontairement les essais.
_echecs: dict = {}          # ip -> nb d'échecs consécutifs
_bloque_jusqua: dict = {}   # ip -> horodatage de fin de blocage
_brute_lock = threading.Lock()
_MAX_ECHECS = int(os.getenv("LOGIN_MAX_ECHECS", "5"))
_BLOCAGE_S = int(os.getenv("LOGIN_BLOCAGE_S", "30"))


def _cle_ip(ip) -> str:
    return str(ip or "inconnue")


def login_bloque(ip=None) -> tuple:
    """(bloqué, secondes restantes) pour cette IP."""
    with _brute_lock:
        restant = _bloque_jusqua.get(_cle_ip(ip), 0.0) - time.time()
        return (restant > 0, max(0, int(restant)))


def enregistrer_echec(ip=None):
    """Après trop d'échecs consécutifs depuis une IP, bloque ses tentatives
    (durée croissante). Les autres IP ne sont pas affectées."""
    cle = _cle_ip(ip)
    with _brute_lock:
        n = _echecs.get(cle, 0) + 1
        _echecs[cle] = n
        if n >= _MAX_ECHECS:
            # blocage croissant : 30s, 60s, 120s... plafonné à 1 h
            duree = _BLOCAGE_S * (2 ** (n - _MAX_ECHECS))
            _bloque_jusqua[cle] = time.time() + min(duree, 3600)
        # borne mémoire : ne pas grossir indéfiniment si l'app est exposée
        if len(_echecs) > 512:
            # Purger AUSSI les IP jamais bloquées (1 à 4 échecs) : elles n'ont
            # aucune entrée dans _bloque_jusqua et ne seraient jamais retirées.
            maintenant = time.time()
            for k in [k for k, t in _bloque_jusqua.items() if t < maintenant]:
                _echecs.pop(k, None)
                _bloque_jusqua.pop(k, None)
            if len(_echecs) > 512:
                for k in [k for k in _echecs if k not in _bloque_jusqua]:
                    _echecs.pop(k, None)


def reinitialiser_echecs(ip=None):
    """Réarme le compteur (connexion réussie). Sans IP, réarme tout."""
    with _brute_lock:
        if ip is None:
            _echecs.clear()
            _bloque_jusqua.clear()
        else:
            cle = _cle_ip(ip)
            _echecs.pop(cle, None)
            _bloque_jusqua.pop(cle, None)


def auth_active() -> bool:
    """L'application demande-t-elle un code d'accès ? NON par défaut.

    Le réglage explicite fait toujours autorité, dans les deux sens. À défaut,
    `APP_PASSWORD` vaut demande d'authentification : quelqu'un qui prend la
    peine d'imposer un code veut évidemment qu'il soit demandé, et sans cette
    règle la variable n'aurait plus aucun effet.
    """
    reglage = os.getenv("APP_AUTH", "").strip().lower()
    if reglage in ("on", "1", "true", "vrai", "oui", "yes"):
        return True
    if reglage in ("off", "0", "false", "faux", "non", "no"):
        return False
    return bool(os.getenv("APP_PASSWORD", "").strip())


_PBKDF2_ITER = int(os.getenv("LOGIN_KDF_ITER", "200000"))


def _hash_legacy(mot: str) -> str:
    """Ancien schéma (SHA-256 non salé) — conservé pour vérifier les
    configurations existantes ; jamais utilisé pour écrire un nouveau hash."""
    return hashlib.sha256(("agence::" + mot).encode()).hexdigest()


def _hash(mot: str, sel: bytes = None, iterations: int = None) -> str:
    """Hash au format « pbkdf2$<iter>$<sel_hex>$<clé_hex> ».

    Un code à 6 chiffres n'a que 10^6 combinaisons : avec un SHA-256 nu, une
    fuite du fichier de config permettrait de le retrouver instantanément hors
    ligne. PBKDF2 (200 000 itérations) rend cette attaque coûteuse.
    """
    sel = sel or secrets.token_bytes(16)
    iterations = iterations or _PBKDF2_ITER
    cle = hashlib.pbkdf2_hmac("sha256", str(mot).encode(), sel, iterations)
    return f"pbkdf2${iterations}${sel.hex()}${cle.hex()}"


def _verifier_contre(mot: str, stocke: str) -> bool:
    """Compare un mot de passe au hash stocké (nouveau format OU hérité)."""
    stocke = stocke or ""
    if stocke.startswith("pbkdf2$"):
        try:
            _, iter_s, sel_hex, _cle = stocke.split("$", 3)
            recalcule = _hash(mot, bytes.fromhex(sel_hex), int(iter_s))
            return hmac.compare_digest(recalcule, stocke)
        except Exception:
            return False
    # Format hérité (SHA-256 non salé)
    return hmac.compare_digest(_hash_legacy(str(mot)), stocke)


def _hash_attendu() -> str:
    # priorité : variable d'env > config locale > défaut
    env = os.getenv("APP_PASSWORD")
    if env:
        return _hash_legacy(env)          # comparé en mode hérité
    h = app_config.get("app_password_hash")
    return h if h else _hash_legacy(DEFAULT_PASSWORD)


def verifier_mot_de_passe(mot: str) -> bool:
    ok = _verifier_contre(str(mot), _hash_attendu())
    # Migration transparente : un mot de passe encore stocké en SHA-256 est
    # ré-enregistré en PBKDF2 dès la première connexion réussie.
    if ok and not os.getenv("APP_PASSWORD"):
        try:
            stocke = app_config.get("app_password_hash")
            if stocke and not str(stocke).startswith("pbkdf2$"):
                app_config.set("app_password_hash", _hash(str(mot)))
        except Exception:
            pass
    return ok


def changer_mot_de_passe(nouveau: str) -> bool:
    nouveau = str(nouveau).strip()
    if len(nouveau) < 4:
        return False
    if os.getenv("APP_PASSWORD"):
        # La variable d'environnement APP_PASSWORD est PRIORITAIRE : écrire un
        # hash en config n'aurait aucun effet. Ne pas prétendre avoir réussi.
        return False
    app_config.set("app_password_hash", _hash(nouveau))
    invalider_sessions()   # un changement de code déconnecte les sessions ouvertes
    return True


def invalider_sessions() -> None:
    """Fait tourner le secret HMAC : tous les cookies émis deviennent invalides.

    Si le code est changé parce qu'il a fuité, les sessions déjà ouvertes par
    un tiers doivent cesser immédiatement — sinon elles resteraient valides
    ~30 jours."""
    global _secret_cache
    with _secret_lock:
        app_config.set("auth_secret", secrets.token_hex(32))
        _secret_cache = None


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    with _secret_lock:
        if _secret_cache:                       # revérifié sous verrou
            return _secret_cache
        s = app_config.get("auth_secret")
        if not s:
            s = secrets.token_hex(32)
            app_config.set("auth_secret", s)
        _secret_cache = s.encode()
        return _secret_cache


def creer_token(jours: int = 30) -> str:
    exp = str(int(time.time()) + jours * 86400)
    sig = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_valide(token: str) -> bool:
    try:
        exp_s, sig = (token or "").split(".", 1)
        if int(exp_s) < time.time():
            return False
        attendu = hmac.new(_secret(), exp_s.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, attendu)
    except Exception:
        return False
