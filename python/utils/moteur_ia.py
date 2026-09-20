"""Moteur d'IA — un seul point de passage pour TOUTES les consultations IA.

L'application est **100 % locale** : deux moteurs, tous deux embarqués et
gratuits, sélectionnables dans l'interface (Réglages ⚙️ ou /setup).

| Moteur   | Où tourne l'IA | Coût    | Ce qu'il faut installer  |
|----------|----------------|---------|--------------------------|
| `ollama` | sur la machine | gratuit | rien (embarqué au build) |
| `hermes` | sur la machine | gratuit | rien (embarqué au build) |

Il n'existe **aucun mode distant** : ni clé API, ni abonnement, ni compte à
créer. Rien de ce que produisent les agents ne quitte la machine.

Avant ce module, `assistant.py` et `orchestrateur.py` construisaient chacun
leur requête Ollama à la main. Ajouter un second moteur aurait dupliqué ce
code une troisième fois — et surtout, le choix de l'utilisateur n'aurait été
respecté qu'à un seul endroit. Tout passe désormais par `chat()`, qui route
selon le moteur sélectionné et retourne du texte (ou None si l'IA n'est pas
joignable — l'appelant garde alors son repli textuel).

Le moteur est mémorisé dans ~/.agence_financiere/config.json (clé
`MOTEUR_IA`) et prend effet immédiatement, sans redémarrage.
"""
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OLLAMA = "ollama"
HERMES = "hermes"
MOTEURS = (OLLAMA, HERMES)
# Tous les moteurs sont locaux. L'alias reste : plusieurs appelants
# distinguaient « local » de « distant », distinction qui n'a plus d'objet.
MOTEURS_LOCAUX = MOTEURS

LIBELLES = {
    OLLAMA: "Ollama (IA locale)",
    HERMES: "Hermès (IA locale)",
}


def _opener():
    """Opener sans proxy système : les moteurs locaux écoutent sur 127.0.0.1,
    qu'un proxy d'entreprise n'a aucune raison d'intercepter."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── Sélection du moteur ───────────────────────────────────────────────
def moteur_actif() -> str:
    """Moteur choisi. Lecture DYNAMIQUE : l'utilisateur peut en changer en
    cours d'exécution depuis l'interface."""
    from config import moteur_ia_actif
    return moteur_ia_actif()


def definir(nom: str) -> Dict[str, Any]:
    """Sélectionne un moteur et le mémorise (environnement + config.json).

    `USE_OLLAMA` reste écrit en parallèle : c'est la clé que lisent les écrans
    de configuration historiques. Elle vaut désormais toujours « true »,
    l'IA étant toujours locale.
    """
    nom = (nom or "").strip().lower()
    if nom not in MOTEURS:
        return {"success": False,
                "error": f"Moteur inconnu : {nom} "
                         f"(moteurs disponibles : {', '.join(MOTEURS)})"}

    os.environ["MOTEUR_IA"] = nom
    os.environ["USE_OLLAMA"] = "true"
    from utils import app_config
    app_config.update(MOTEUR_IA=nom, USE_OLLAMA="true")
    logger.info(f"[IA] Moteur sélectionné : {nom}")
    return {"success": True, "moteur": nom}


def _embarque(nom: str):
    """Gestionnaire du moteur demandé (None si le nom est inconnu)."""
    if nom == OLLAMA:
        from utils.ollama_embedded import get_ollama_embedded
        return get_ollama_embedded()
    if nom == HERMES:
        from utils.hermes_embedded import get_hermes_embedded
        return get_hermes_embedded()
    return None


def disponible(nom: str = "") -> bool:
    """Le moteur peut-il répondre MAINTENANT ?"""
    nom = nom or moteur_actif()
    gestionnaire = _embarque(nom)
    try:
        return bool(gestionnaire and gestionnaire.serveur_repond())
    except Exception:
        return False


def demarrer_si_possible(nom: str = ""):
    """Démarre le moteur sélectionné (appelé au boot de l'application).

    Seul le moteur CHOISI est démarré : lancer les deux chargerait deux
    modèles en mémoire — plusieurs gigaoctets pour rien sur une machine qui
    fait déjà tourner MetaTrader.
    """
    nom = nom or moteur_actif()
    gestionnaire = _embarque(nom)
    if gestionnaire is None:
        return
    try:
        gestionnaire.demarrer_si_possible()
    except Exception as e:
        logger.warning(f"[IA] Démarrage du moteur {nom} : {e}")


# ── Consultation ──────────────────────────────────────────────────────
def chat(messages: List[Dict[str, str]], timeout: float = 30.0,
         moteur: str = "", temperature: float = 0.2) -> Optional[str]:
    """Pose une question au moteur sélectionné. Retourne le texte, ou None.

    `messages` suit la convention OpenAI/Ollama : [{"role": …, "content": …}].
    None signifie « pas de réponse exploitable » — jamais une exception : les
    appelants (assistants, Chef d'Orchestre) disposent tous d'un repli
    textuel, et une IA injoignable ne doit PAS interrompre un cycle d'analyse.
    """
    nom = moteur or moteur_actif()
    try:
        if nom == HERMES:
            return _chat_hermes(messages, timeout, temperature)
        return _chat_ollama(messages, timeout, temperature)
    except Exception as e:
        logger.debug(f"[IA/{nom}] Consultation impossible : {e}")
        return None


def _post_json(url: str, charge: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(charge).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with _opener().open(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _chat_ollama(messages, timeout, temperature) -> Optional[str]:
    from config import OLLAMA_URL, ollama_model_actif
    reponse = _post_json(f"{OLLAMA_URL}/api/chat", {
        "model": ollama_model_actif(),
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }, timeout)
    texte = (reponse.get("message") or {}).get("content", "")
    return texte.strip() or None


def _chat_hermes(messages, timeout, temperature) -> Optional[str]:
    """API compatible OpenAI exposée par llama-server."""
    from config import HERMES_URL
    reponse = _post_json(f"{HERMES_URL}/v1/chat/completions", {
        # llama-server ne sert qu'UN modèle, chargé au démarrage : le champ
        # est ignoré côté serveur mais exigé par le format OpenAI.
        "model": "hermes",
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        # Les réponses attendues font une à deux phrases. Sans plafond,
        # llama-server peut dérouler jusqu'à saturer la fenêtre de contexte
        # et dépasser le délai des agents.
        "max_tokens": 256,
    }, timeout)
    choix = reponse.get("choices") or []
    if not choix:
        return None
    texte = (choix[0].get("message") or {}).get("content", "")
    return texte.strip() or None


# ── État pour l'interface ─────────────────────────────────────────────
def statut() -> Dict[str, Any]:
    """Vue d'ensemble des deux moteurs — alimente l'écran de configuration.

    Rassemble en UN appel ce que l'interface devait auparavant recomposer
    depuis plusieurs endpoints différents (et ne savait pas faire pour Hermès).
    """
    from config import (HERMES_URL, OLLAMA_URL,
                        hermes_model_actif, ollama_model_actif)
    from utils import runtime_embarque

    actif = moteur_actif()
    embarques = runtime_embarque.resume()

    try:
        from utils.ollama_embedded import get_ollama_embedded
        ollama = get_ollama_embedded()
        etat_ollama = {
            "installe": ollama.est_disponible(),
            "serveur_actif": ollama.serveur_repond(),
            "modele": ollama_model_actif(),
            "embarque": embarques.get("ollama", False),
        }
    except Exception as e:
        etat_ollama = {"installe": False, "serveur_actif": False, "erreur": str(e)}

    try:
        from utils.hermes_embedded import get_hermes_embedded
        hermes = get_hermes_embedded()
        etat_hermes = {
            "installe": hermes.est_installe(),
            "serveur_actif": hermes.serveur_repond(),
            "modele": hermes_model_actif(),
            "embarque": embarques.get("hermes", False) and embarques.get("hermes_modele", False),
        }
    except Exception as e:
        etat_hermes = {"installe": False, "serveur_actif": False, "erreur": str(e)}

    return {
        "moteur": actif,
        "libelle": LIBELLES.get(actif, actif),
        "moteurs": [
            {"cle": OLLAMA, "libelle": LIBELLES[OLLAMA], "local": True,
             "url": OLLAMA_URL, **etat_ollama},
            {"cle": HERMES, "libelle": LIBELLES[HERMES], "local": True,
             "url": HERMES_URL, **etat_hermes},
        ],
        "runtime_embarque": embarques,
    }
