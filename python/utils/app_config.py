"""Configuration partagée (~/.agence_financiere/config.json).

Point d'accès UNIQUE à la configuration, pour éviter que plusieurs
modules (auth, notifier, setup, ollama) s'écrasent mutuellement leurs
clés en écrivant chacun le fichier de leur côté.

- Verrou de processus : pas de perte de mise à jour concurrente.
- Écriture atomique (fichier temporaire + os.replace) : une coupure en
  plein enregistrement ne corrompt plus le fichier (avant, un write_text
  interrompu laissait un config.json vide → mot de passe et secret de
  session perdus).
"""
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_PATH = Path(os.path.expanduser("~")) / ".agence_financiere" / "config.json"
_lock = threading.RLock()
_cache = None  # dict | None


def _nettoyer_obsolete(cfg: dict) -> bool:
    """Retire les clés d'anciennes fonctionnalités supprimées. Retourne True si
    quelque chose a été retiré (→ le fichier doit être réécrit).

    Le pont cloud MetaApi puis le mode IA distant ont été retirés : leurs clés
    résiduelles n'ont plus d'effet, mais on les efface pour ne pas laisser
    traîner un jeton d'API dans le fichier de configuration. Une clé Anthropic
    enregistrée par une version précédente est donc EFFACÉE DU DISQUE au
    premier démarrage — l'application ne s'en sert plus, elle n'a aucune raison
    de continuer à la stocker."""
    change = False
    for cle in ("metaapi_token", "metaapi_account_id", "ANTHROPIC_API_KEY"):
        if cle in cfg:
            cfg.pop(cle, None)
            change = True
    # « cloud » désignait le mode distant, qui n'existe plus : le laisser
    # ferait désigner par la config un moteur introuvable.
    if str(cfg.get("MOTEUR_IA", "")).lower() == "cloud":
        cfg["MOTEUR_IA"] = "ollama"
        cfg["USE_OLLAMA"] = "true"
        change = True
    comptes = cfg.get("comptes")
    if isinstance(comptes, list):
        restants = [c for c in comptes if isinstance(c, dict) and c.get("type") != "cloud"]
        if len(restants) != len(comptes):
            cfg["comptes"] = restants
            if str(cfg.get("compte_actif", "")).startswith("cloud:"):
                cfg["compte_actif"] = ""
            change = True
    return change


def _charger() -> dict:
    global _cache
    if _cache is None:
        if not _PATH.exists():
            _cache = {}
            return _cache
        try:
            data = json.loads(_PATH.read_text())
            _cache = data if isinstance(data, dict) else {}
        except Exception as e:
            # Fichier présent mais illisible : le mettre de côté pour
            # diagnostic AU LIEU de repartir en silence sur une config vide
            # (sinon : retour au mot de passe par défaut + déconnexion de
            # toutes les sessions, sans que l'utilisateur le sache).
            try:
                # Nom horodaté : un second incident n'écrase pas le premier.
                import time as _t
                backup = _PATH.with_name(f"{_PATH.name}.corrupt.{int(_t.time())}")
                _PATH.replace(backup)
                # Le fichier contient auth_secret + identifiants : le verrouiller
                # (il héritait sinon des permissions d'origine, potentiellement 0644).
                try:
                    os.chmod(backup, 0o600)
                except Exception:
                    pass
                logger.error(f"[config] fichier illisible ({e}) — sauvegardé dans "
                             f"{backup}. Reconfiguration nécessaire.")
            except Exception:
                logger.error(f"[config] fichier illisible ({e})")
            _cache = {}
            return _cache
        # Nettoyage HORS du try de parsing (une entrée inattendue ne doit pas
        # faire passer une config valide pour « illisible ») et PERSISTÉ : la
        # docstring promet d'effacer le jeton du fichier, pas seulement du cache.
        try:
            if _nettoyer_obsolete(_cache):
                _ecrire(_cache)
        except Exception as e:
            logger.warning(f"[config] nettoyage des clés obsolètes : {e}")
    return _cache


def get(cle: str, defaut=None):
    with _lock:
        return _charger().get(cle, defaut)


def tout() -> dict:
    with _lock:
        return dict(_charger())


def _ecrire(cfg: dict) -> None:
    """Écriture atomique et durable du fichier de config (déjà sous _lock).

    - Le tmp est créé DÈS L'ORIGINE en 0600 (os.open) : le fichier contient
      des secrets (identifiants courtier, secret de session HMAC, webhook),
      il ne doit jamais être lisible par d'autres utilisateurs, même
      transitoirement.
    - fsync du fichier PUIS du répertoire avant/après le rename : une coupure
      de courant ne peut plus laisser un config.json vide/tronqué (sinon :
      mot de passe réinitialisé, sessions déconnectées, comptes perdus).
    """
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    # Nom de tmp UNIQUE par processus : le RLock ne protège que dans UN
    # processus, or un double-clic sur l'exe peut lancer une seconde instance
    # (port suivant). Un « .tmp » partagé les ferait s'écraser mutuellement en
    # pleine écriture ; os.replace reste atomique par instance.
    tmp = _PATH.with_name(f"{_PATH.name}.{os.getpid()}.tmp")
    donnees = json.dumps(cfg)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, donnees.encode("utf-8"))
        os.fsync(fd)                      # contenu réellement sur le disque
    finally:
        os.close(fd)
    os.replace(tmp, _PATH)                # remplacement atomique
    try:
        os.chmod(_PATH, 0o600)            # au cas où le fichier final préexistait
    except Exception:
        pass
    try:
        dfd = os.open(str(_PATH.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)                 # le rename lui-même est durable
        finally:
            os.close(dfd)
    except Exception:
        pass


def update(**kv) -> dict:
    """Fusionne les clés fournies en préservant les autres, écriture atomique."""
    with _lock:
        cfg = _charger()
        cfg.update(kv)
        _ecrire(cfg)
        return dict(cfg)


def mutate(fn) -> dict:
    """Applique fn(cfg) SOUS VERROU (lecture-modification-écriture atomique).

    Indispensable pour les mises à jour de type liste (comptes) : un get()
    suivi d'un set() séparés perdraient une écriture concurrente."""
    with _lock:
        cfg = _charger()
        fn(cfg)
        _ecrire(cfg)
        return dict(cfg)


def set(cle: str, valeur) -> dict:
    return update(**{cle: valeur})
