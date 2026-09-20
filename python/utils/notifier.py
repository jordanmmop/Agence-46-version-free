"""Notifications — alertes sur les événements importants.

Deux canaux, cumulables :
- Webhook (Discord, Slack, ou compatible) : un message dans un salon.
- Push mobile via ntfy.sh : une VRAIE notification sur le téléphone
  (application ntfy, iOS + Android, gratuite, sans compte). L'utilisateur
  choisit un nom de sujet unique ; le serveur y publie, le téléphone y est
  abonné. On peut aussi pointer un serveur ntfy auto-hébergé.

Auto-contenu : aucun service tiers requis côté serveur, aucun identifiant
stocké en clair dans le dépôt.

Événements notifiés : ordre exécuté, kill-switch de sécurité déclenché,
erreur critique de l'auto-trader, rapport quotidien.
"""
import json
import logging
import os
import re
import threading
import urllib.request

from utils import app_config

logger = logging.getLogger(__name__)

# Le webhook (droit de poster dans un salon) et le sujet ntfy (secret partagé)
# sont des credentials : on les stocke obfusqués, jamais en clair. Le préfixe
# « obf: » distingue les valeurs obfusquées des valeurs héritées en clair
# (migration transparente au prochain enregistrement).
_OBF = "obf:"


def _lire_secret(cle: str) -> str:
    v = app_config.get(cle, "") or ""
    if v.startswith(_OBF):
        from utils.mt5_manager import MT5Manager
        return MT5Manager._desobfusquer(v[len(_OBF):])
    return v   # valeur héritée en clair


def _ecrire_secret(cle: str, valeur: str) -> None:
    valeur = (valeur or "").strip()
    if not valeur:
        app_config.set(cle, "")
        return
    from utils.mt5_manager import MT5Manager
    app_config.set(cle, _OBF + MT5Manager._obfusquer(valeur))


# ── Webhook (Discord / Slack) ─────────────────────────────────────────
def get_webhook() -> str:
    return os.getenv("WEBHOOK_URL") or _lire_secret("webhook_url")


def set_webhook(url: str) -> bool:
    """Enregistre l'URL du webhook. HTTPS obligatoire — même règle que le sujet
    ntfy, et pour la même raison : le message contient les ordres passés et
    l'état du compte. En http:// il circulerait en clair, et « http » suffisait
    auparavant à faire accepter n'importe quelle adresse du réseau interne
    (le serveur y émettant alors un POST — SSRF)."""
    # Une valeur non textuelle est REFUSÉE, pas convertie : `(url or "").strip()`
    # levait AttributeError sur un nombre ou une liste (rendu en 500 « Erreur
    # interne »), et str() en aurait fait une pseudo-URL acceptée à tort.
    if url is not None and not isinstance(url, str):
        return False
    url = (url or "").strip()
    if url and not url.lower().startswith("https://"):
        return False
    _ecrire_secret("webhook_url", url)
    return True


# ── Push mobile (ntfy.sh) ─────────────────────────────────────────────
def get_ntfy() -> str:
    """Sujet ntfy configuré (nom de sujet ou URL complète)."""
    return os.getenv("NTFY_TOPIC") or _lire_secret("ntfy_topic")


def set_ntfy(topic: str) -> bool:
    """Enregistre le sujet ntfy. Valide la valeur : soit un nom de sujet simple,
    soit une URL HTTPS. On refuse le http:// en clair et les schémas exotiques
    (le serveur ferait sinon une requête POST vers une adresse arbitraire —
    SSRF vers le réseau interne)."""
    # Non textuel = refusé (cf. set_webhook) : .strip() levait AttributeError.
    if topic is not None and not isinstance(topic, str):
        return False
    topic = (topic or "").strip()
    if topic:
        bas = topic.lower()
        if bas.startswith("http://"):
            return False                      # non chiffré → refusé
        if bas.startswith("https://"):
            pass                              # serveur ntfy auto-hébergé : OK
        elif "://" in topic or "/" in topic:
            return False                      # schéma exotique / chemin douteux
        elif not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
            return False                      # nom de sujet : alphanumérique
    _ecrire_secret("ntfy_topic", topic)
    return True


def _ntfy_url() -> str:
    t = get_ntfy()
    if not t:
        return ""
    if t.startswith("http://") or t.startswith("https://"):
        return t
    base = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    return f"{base}/{t}"


def _envoyer_ntfy(texte: str):
    url = _ntfy_url()
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=texte.encode("utf-8"),
            headers={
                "Title": "Agence IA",
                "Tags": "chart_with_upwards_trend",
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(req, timeout=8)
    except Exception as e:
        logger.warning(f"[Notifier] Échec push ntfy : {e}")


def _envoyer_webhook(texte: str):
    url = get_webhook()
    if not url:
        return
    try:
        # Format Discord/Slack : {"content": ...} marche pour Discord ;
        # Slack attend {"text": ...}. On envoie les deux clés.
        payload = json.dumps({"content": texte, "text": texte}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(req, timeout=8)
    except Exception as e:
        logger.warning(f"[Notifier] Échec envoi webhook : {e}")


# ── API publique ──────────────────────────────────────────────────────
def canaux_configures() -> bool:
    """Vrai si au moins un canal (webhook ou push mobile) est configuré."""
    return bool(get_webhook() or get_ntfy())


def _envoyer_tous(texte: str):
    _envoyer_webhook(texte)
    _envoyer_ntfy(texte)


def notifier(texte: str):
    """Envoi non bloquant (thread) — n'interrompt jamais le trading."""
    if not canaux_configures():
        return
    threading.Thread(target=_envoyer_tous, args=(f"🤖 Agence IA — {texte}",),
                     daemon=True).start()


def tester() -> dict:
    """Envoie un message de test sur tous les canaux configurés."""
    if not canaux_configures():
        return {"success": False, "error": "Aucun canal configuré (webhook ou push mobile)"}
    _envoyer_tous("🤖 Agence IA — test de notification ✅")
    canaux = []
    if get_webhook():
        canaux.append("webhook")
    if get_ntfy():
        canaux.append("push mobile (ntfy)")
    return {"success": True, "canaux": canaux}
