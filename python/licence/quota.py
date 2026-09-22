"""Compteur de requêtes IA de la version d'essai.

OÙ VIT LE COMPTEUR
------------------
Dans la base SQLite du serveur applicatif (`~/.agence_financiere/data/agence.db`),
rattaché à un COMPTE (cf. `abonnement.compte_id`) — pas au navigateur.

C'est la différence qui compte : un compteur en `localStorage` se remet à zéro
d'un clic dans les outils de développement, ou simplement en ouvrant
l'application depuis un autre appareil du même Wi-Fi (l'application écoute sur
le réseau local, c'est documenté). Le compteur est donc tenu côté serveur, et
l'interface ne fait que L'AFFICHER.

CE QUI EST COMPTÉ
-----------------
Une entrée par cycle d'analyse demandé (un appel à /api/analyser). Les fenêtres
glissantes — 24 h et 1 h — sont calculées à la lecture : pas de remise à zéro
à minuit à programmer, et changer d'heure d'été ne fabrique ni ne détruit de
requêtes.

LIMITE CONNUE
-------------
Ce compteur est LOCAL à l'installation. Réinstaller l'application repart d'un
compteur neuf — comme tout logiciel de bureau dont le serveur tourne sur la
machine de l'utilisateur. Un comptage réellement inviolable suppose que les
requêtes passent par un service distant ; l'architecture est prête pour cela
(`abonnement.url_emetteur`), mais ce service n'existe pas encore.
"""
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TABLE = "licence_requetes"


def _db():
    from utils.database import Database
    return Database()


def _assurer_table(conn) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            compte TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'analyse',
            horodatage REAL NOT NULL
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_compte "
                 f"ON {_TABLE}(compte, horodatage)")


def _compter(conn, compte: str, depuis: float) -> int:
    ligne = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE compte = ? AND horodatage >= ?",
        (compte, depuis),
    ).fetchone()
    return int(ligne["n"] if ligne else 0)


def _purger(conn, avant: float) -> None:
    """Les entrées de plus de 24 h ne servent plus à aucun calcul. Sans purge,
    la table grossirait indéfiniment au fil des mois."""
    conn.execute(f"DELETE FROM {_TABLE} WHERE horodatage < ?", (avant,))


def utilisation(compte: str) -> Dict[str, int]:
    """Nombre de requêtes sur les 24 dernières heures et la dernière heure."""
    maintenant = time.time()
    try:
        with _db()._tx() as conn:
            _assurer_table(conn)
            _purger(conn, maintenant - 25 * 3600)
            return {
                "jour": _compter(conn, compte, maintenant - 24 * 3600),
                "heure": _compter(conn, compte, maintenant - 3600),
            }
    except Exception as e:
        # Une base illisible ne doit pas bloquer l'application ; on le
        # journalise et on repart d'un compteur vide pour cette lecture.
        logger.warning("[licence] Compteur de requêtes illisible : %s", e)
        return {"jour": 0, "heure": 0}


def enregistrer(compte: str, type_requete: str = "analyse") -> Dict[str, int]:
    """Ajoute une requête et renvoie l'utilisation MISE À JOUR."""
    maintenant = time.time()
    try:
        with _db()._tx() as conn:
            _assurer_table(conn)
            conn.execute(
                f"INSERT INTO {_TABLE} (compte, type, horodatage) VALUES (?, ?, ?)",
                (compte, str(type_requete)[:32], maintenant),
            )
            _purger(conn, maintenant - 25 * 3600)
            return {
                "jour": _compter(conn, compte, maintenant - 24 * 3600),
                "heure": _compter(conn, compte, maintenant - 3600),
            }
    except Exception as e:
        logger.warning("[licence] Requête non comptabilisée : %s", e)
        return utilisation(compte)


def restant(utilise: int, limite: Optional[int]) -> Optional[int]:
    """Requêtes restantes, ou None quand la limite est illimitée (Pro)."""
    if limite is None:
        return None
    return max(0, limite - int(utilise or 0))


def reinitialiser(compte: str) -> int:
    """Efface le compteur d'un compte. Réservé aux tests et au support : AUCUNE
    route HTTP ne l'expose — sinon la limite d'essai se lèverait d'un clic."""
    try:
        with _db()._tx() as conn:
            _assurer_table(conn)
            cur = conn.execute(f"DELETE FROM {_TABLE} WHERE compte = ?", (compte,))
            return int(cur.rowcount or 0)
    except Exception as e:
        logger.warning("[licence] Réinitialisation impossible : %s", e)
        return 0


def details(compte: str, limite_jour: Optional[int],
            limite_heure: Optional[int]) -> Dict[str, Any]:
    """Bloc « quotas » tel que l'interface l'affiche."""
    u = utilisation(compte)
    return {
        "jour_utilise": u["jour"],
        "jour_limite": limite_jour,
        "jour_restant": restant(u["jour"], limite_jour),
        "heure_utilise": u["heure"],
        "heure_limite": limite_heure,
        "heure_restant": restant(u["heure"], limite_heure),
        "illimite": limite_jour is None and limite_heure is None,
        # Phrase prête à afficher — même formulation que l'exemple du cahier
        # des charges : « 18 / 20 requêtes utilisées aujourd'hui ».
        "resume": ("Requêtes illimitées" if limite_jour is None
                   else f"{u['jour']} / {limite_jour} requêtes utilisées aujourd'hui"),
    }
