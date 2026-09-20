"""Multi-comptes AvaTrade : mémorise plusieurs comptes MetaTrader 5 et permet
de basculer de l'un à l'autre sans ressaisir les identifiants.

Chaque connexion réussie est enregistrée automatiquement comme compte
basculable (clé stable par numéro de compte → pas de doublon). Les secrets
(mot de passe MT5) sont obfusqués avec le même schéma que
mt5_manager — jamais en clair dans le fichier de config.
"""
import logging
from typing import Any, Dict, List

from utils import app_config

logger = logging.getLogger(__name__)


def _obf(txt: str) -> str:
    from utils.mt5_manager import MT5Manager
    return MT5Manager._obfusquer(txt or "")


def _comptes() -> List[Dict]:
    c = app_config.get("comptes")
    return list(c) if isinstance(c, list) else []


def _apercu(c: Dict) -> str:
    return f"MT5 · compte {c.get('account', '')} ({c.get('account_type', 'demo')})"


def lister() -> Dict[str, Any]:
    """Liste des comptes (SANS aucun secret) + identifiant du compte actif."""
    actif = app_config.get("compte_actif", "")
    comptes = [{
        "id": c.get("id"),
        "label": c.get("label") or "(sans nom)",
        "type": c.get("type"),
        "apercu": _apercu(c),
        "actif": c.get("id") == actif,
    } for c in _comptes()]
    return {"comptes": comptes, "actif": actif}


def _upsert_in(cfg: Dict, entry: Dict) -> None:
    """Insère/met à jour un compte DANS cfg (appelé sous verrou via mutate)."""
    comptes = cfg.get("comptes")
    if not isinstance(comptes, list):
        comptes = []
    for i, c in enumerate(comptes):
        if c.get("id") == entry["id"]:
            entry["label"] = c.get("label") or entry["label"]  # garde le nom choisi
            comptes[i] = entry
            break
    else:
        comptes.append(entry)
    cfg["comptes"] = comptes


def memoriser_mt5(account, password, account_type, server) -> None:
    """Rend un compte MT5 basculable après une connexion réussie."""
    try:
        entry = {
            "id": f"mt5:{account}",
            "type": "mt5",
            "label": f"AvaTrade {account} ({account_type})",
            "account": str(account),
            "password": _obf(str(password)),
            "account_type": account_type,
            "server": server,
        }
        # Upsert + compte_actif dans UNE SEULE mutation atomique (pas de course)
        def _fn(cfg):
            _upsert_in(cfg, entry)
            cfg["compte_actif"] = entry["id"]
        app_config.mutate(_fn)
    except Exception as e:
        logger.warning(f"[Comptes] mémorisation MT5 : {e}")


def renommer(cid: str, label: str) -> Dict[str, Any]:
    trouve = {"ok": False}
    def _fn(cfg):
        for c in cfg.get("comptes") or []:
            if c.get("id") == cid:
                c["label"] = (label or "").strip() or c.get("label")
                trouve["ok"] = True
    app_config.mutate(_fn)
    return {"success": True} if trouve["ok"] else {"success": False, "error": "Compte introuvable"}


def supprimer(cid: str) -> Dict[str, Any]:
    def _fn(cfg):
        cfg["comptes"] = [c for c in (cfg.get("comptes") or []) if c.get("id") != cid]
        if cfg.get("compte_actif") == cid:
            cfg["compte_actif"] = ""
    app_config.mutate(_fn)
    return {"success": True}


def basculer(cid: str) -> Dict[str, Any]:
    """Bascule sur le compte demandé : coupe la session courante puis
    reconnecte avec les identifiants mémorisés du compte choisi."""
    c = next((x for x in _comptes() if x.get("id") == cid), None)
    if not c:
        return {"success": False, "error": "Compte introuvable"}

    from utils.mt5_manager import get_mt5_manager
    mt5 = get_mt5_manager()
    try:
        mt5.disconnect()
    except Exception:
        pass

    app_config.update(mt5_creds={
        "account": c.get("account"),
        "password": c.get("password"),          # déjà obfusqué (même schéma)
        "account_type": c.get("account_type", "demo"),
        "server": c.get("server", ""),
    })
    r = mt5.reconnexion_auto()

    if r.get("success"):
        app_config.set("compte_actif", cid)
    return r
