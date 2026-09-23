"""Comptes utilisateurs stockés sur une base PostgreSQL distante.

POURQUOI
--------
Par défaut, les comptes vivent dans le fichier SQLite de la machine qui fait
tourner l'application. C'est parfait pour une installation de bureau, et
inadapté dès qu'il faut une base CENTRALE : un compte doit alors suivre son
propriétaire d'un appareil à l'autre, et survivre à une réinstallation.

Ce module fournit la même chose que `licence.comptes.DepotComptes` — les
MÊMES treize méthodes, le même contrat — sur une base PostgreSQL. Aucun autre
fichier du projet ne change : `comptes.depot()` choisit l'implémentation selon
la présence de `AGENCE_COMPTES_DSN`.

ACTIVATION
----------
    AGENCE_COMPTES_DSN=postgresql://agence:MOTDEPASSE@[2001:41d0:301::21]:5432/agence

⚠️ Une adresse IPv6 littérale DOIT être entre crochets : sans eux, les
deux-points de l'adresse sont lus comme le séparateur du port, et la connexion
échoue avec un message qui n'aide pas (« invalid port number »).

CHIFFREMENT DU TRANSPORT
------------------------
La connexion transporte des mots de passe hachés, des jetons de session et des
identités. Vers un hôte distant, TLS est donc EXIGÉ : si le DSN ne précise pas
`sslmode`, ce module ajoute `sslmode=require`. Un `sslmode=disable` explicite
vers une adresse non locale est refusé — mieux vaut un démarrage qui échoue en
le disant qu'une base de comptes qui circule en clair.

CE QUI N'EST PAS STOCKÉ
-----------------------
Aucune donnée de carte bancaire, ici comme ailleurs. Voir `licence/comptes.py`.
"""
import logging
import os
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from licence.comptes import ContactDejaUtilise

logger = logging.getLogger(__name__)

VARIABLE_DSN = "AGENCE_COMPTES_DSN"

# Hôtes pour lesquels TLS n'est pas imposé : la connexion ne quitte pas la
# machine, il n'y a pas de réseau à écouter.
_HOTES_LOCAUX = {"localhost", "127.0.0.1", "::1", "", None}


def dsn_configure() -> str:
    return (os.getenv(VARIABLE_DSN, "") or "").strip()


def _hote(dsn: str) -> str:
    """Hôte d'un DSN, crochets IPv6 retirés. Chaîne vide si illisible."""
    try:
        hote = urlsplit(dsn).hostname          # retire déjà les crochets
    except ValueError:
        return ""
    return (hote or "").lower()


def _refuser_ipv6_sans_crochets(dsn: str) -> None:
    """Rejette une adresse IPv6 écrite sans crochets, avec la correction.

    C'est LE piège de ce réglage, et il ne se signale pas tout seul :
    `urlsplit` ne lève AUCUNE erreur sur

        postgresql://agence:mdp@2001:41d0:301::21:5432/agence

    Il lit tranquillement « 2001 » comme nom d'hôte et le reste comme port.
    La configuration paraît acceptée, puis la connexion échoue plus tard sur
    un « could not translate host name "2001" » que personne ne rattache à un
    défaut de ponctuation. On tranche donc ici, avant toute connexion.
    """
    _, _, apres_schema = dsn.partition("://")
    netloc = apres_schema.split("/", 1)[0]
    hote_port = netloc.rsplit("@", 1)[-1]   # retire utilisateur:motdepasse
    if hote_port.startswith("["):
        return                               # déjà entre crochets
    if hote_port.count(":") <= 1:
        return                               # « hote » ou « hote:port »
    # Au moins deux « : » hors crochets : c'est une IPv6 nue.
    adresse = hote_port.rsplit(":", 1)[0] if hote_port.count(":") > 1 else hote_port
    raise ValueError(
        f"{VARIABLE_DSN} : l'adresse IPv6 doit être entre crochets.\n"
        f"  Reçu    : …@{hote_port}/…\n"
        f"  Attendu : …@[{adresse}]:5432/…\n"
        f"  Sans les crochets, les deux-points de l'adresse sont lus comme le "
        f"séparateur du port et la connexion échoue plus tard, sur un message "
        f"sans rapport.")


def preparer_dsn(dsn: str) -> str:
    """Complète le DSN avec les garanties minimales, ou lève ValueError.

    Deux contrôles, tous deux faits AVANT la première connexion :

    - une adresse IPv6 sans crochets est rejetée avec une explication, plutôt
      que de laisser psycopg répondre « invalid port number » ;
    - TLS est imposé vers un hôte distant.
    """
    dsn = (dsn or "").strip()
    if not dsn:
        raise ValueError(f"{VARIABLE_DSN} est vide.")

    _refuser_ipv6_sans_crochets(dsn)

    try:
        parties = urlsplit(dsn)
        parties.port                        # lève ValueError sur un port absurde
    except ValueError as e:
        raise ValueError(
            f"{VARIABLE_DSN} illisible ({e}). Si l'hôte est une adresse IPv6, "
            f"elle doit être entre crochets :\n"
            f"  postgresql://utilisateur:motdepasse@[2001:41d0:301::21]:5432/agence"
        ) from e

    if parties.scheme not in ("postgresql", "postgres"):
        raise ValueError(
            f"{VARIABLE_DSN} doit commencer par « postgresql:// » "
            f"(reçu : « {parties.scheme}:// »).")

    hote = _hote(dsn)
    if not hote:
        raise ValueError(f"{VARIABLE_DSN} ne nomme aucun hôte.")

    distant = hote not in _HOTES_LOCAUX
    if "sslmode=" not in dsn:
        if distant:
            separateur = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{separateur}sslmode=require"
            logger.info("[comptes] TLS imposé vers %s (sslmode=require ajouté)", hote)
    elif distant and "sslmode=disable" in dsn:
        raise ValueError(
            "sslmode=disable vers un hôte distant est refusé : la base des "
            "comptes transporte des mots de passe hachés et des jetons de "
            "session, qui circuleraient en clair. Utilisez sslmode=require "
            "(ou verify-full avec un certificat).")
    return dsn


class DepotPostgres:
    """Même contrat que `DepotComptes`, sur PostgreSQL.

    Les connexions sont tenues par un POOL : ouvrir une connexion par requête
    — ce que fait la version SQLite, sans conséquence sur un fichier local —
    coûterait ici un aller-retour réseau et une poignée de main TLS à chaque
    vérification de session, c'est-à-dire à chaque requête HTTP.
    """

    def __init__(self, dsn: str = "", taille_max: int = 8):
        self._dsn = preparer_dsn(dsn or dsn_configure())
        self._taille_max = max(1, int(taille_max))
        self._pool = None
        self._tables_creees = False
        self._lock = threading.Lock()

    # ── Connexion ──
    def _obtenir_pool(self):
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                from psycopg_pool import ConnectionPool
                from psycopg.rows import dict_row
            except ImportError as e:
                raise RuntimeError(
                    "Le pilote PostgreSQL est absent. Installez-le :\n"
                    "    pip install 'psycopg[binary,pool]'") from e
            # `open=True` + `timeout` : un serveur injoignable doit échouer
            # vite et clairement, pas figer la première requête HTTP.
            self._pool = ConnectionPool(
                self._dsn, min_size=1, max_size=self._taille_max,
                timeout=15, kwargs={"row_factory": dict_row}, open=True,
            )
            return self._pool

    def _connexion(self):
        return self._obtenir_pool().connection()

    def _assurer_tables(self, conn) -> None:
        if self._tables_creees:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS comptes (
                    id                 TEXT PRIMARY KEY,
                    email              TEXT NOT NULL,
                    telephone          TEXT NOT NULL,
                    email_cle          TEXT NOT NULL,
                    telephone_cle      TEXT NOT NULL,
                    mot_de_passe       TEXT NOT NULL,
                    nom                TEXT DEFAULT '',
                    adresse            TEXT DEFAULT '',
                    code_postal        TEXT DEFAULT '',
                    ville              TEXT DEFAULT '',
                    pays               TEXT DEFAULT '',
                    cree_le            DOUBLE PRECISION NOT NULL,
                    essai_fin          DOUBLE PRECISION NOT NULL,
                    etat               TEXT NOT NULL DEFAULT 'TRIAL',
                    formule            TEXT DEFAULT '',
                    abonne_jusqua      DOUBLE PRECISION,
                    paiement_ref       TEXT DEFAULT '',
                    derniere_connexion DOUBLE PRECISION
                )""")
            # Unicité tenue par le MOTEUR, comme en SQLite : deux inscriptions
            # simultanées depuis deux machines ne peuvent pas passer toutes
            # les deux. C'est le point que la centralisation doit préserver.
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_comptes_email_cle "
                        "ON comptes(email_cle)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_comptes_telephone_cle "
                        "ON comptes(telephone_cle)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS comptes_sessions (
                    jeton     TEXT PRIMARY KEY,
                    compte_id TEXT NOT NULL,
                    cree_le   DOUBLE PRECISION NOT NULL,
                    expire_le DOUBLE PRECISION NOT NULL
                )""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_compte "
                        "ON comptes_sessions(compte_id)")
        conn.commit()
        self._tables_creees = True

    def _executer(self, sql: str, params: tuple = (), *, fetch: str = ""):
        with self._connexion() as conn:
            self._assurer_tables(conn)
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "un":
                    ligne = cur.fetchone()
                    return dict(ligne) if ligne else None
                if fetch == "valeur":
                    ligne = cur.fetchone()
                    return list(ligne.values())[0] if ligne else None
                return None

    # ── Lectures ──
    def par_email(self, email: str) -> Optional[Dict[str, Any]]:
        from licence.comptes import cle_email
        return self._executer("SELECT * FROM comptes WHERE email_cle = %s",
                              (cle_email(email),), fetch="un")

    def par_telephone(self, telephone: str) -> Optional[Dict[str, Any]]:
        from licence.comptes import cle_telephone
        return self._executer("SELECT * FROM comptes WHERE telephone_cle = %s",
                              (cle_telephone(telephone),), fetch="un")

    def par_id(self, compte_id: str) -> Optional[Dict[str, Any]]:
        return self._executer("SELECT * FROM comptes WHERE id = %s",
                              (compte_id,), fetch="un")

    def nombre(self) -> int:
        return int(self._executer("SELECT COUNT(*) AS n FROM comptes",
                                  fetch="valeur") or 0)

    # ── Écritures ──
    def creer(self, compte: Dict[str, Any]) -> None:
        """Lève `ContactDejaUtilise` si l'e-mail ou le téléphone existe déjà."""
        import psycopg
        colonnes = ", ".join(compte)
        marques = ", ".join(["%s"] * len(compte))
        try:
            self._executer(
                f"INSERT INTO comptes ({colonnes}) VALUES ({marques})",
                tuple(compte.values()))
        except psycopg.errors.UniqueViolation as e:
            raise ContactDejaUtilise(str(e)) from e

    def modifier(self, compte_id: str, **champs) -> None:
        if not champs:
            return
        sets = ", ".join(f"{k} = %s" for k in champs)
        self._executer(f"UPDATE comptes SET {sets} WHERE id = %s",
                       (*champs.values(), compte_id))

    # ── Sessions ──
    def creer_session(self, jeton: str, compte_id: str, duree_s: float) -> None:
        import time
        maintenant = time.time()
        with self._connexion() as conn:
            self._assurer_tables(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM comptes_sessions WHERE expire_le < %s",
                            (maintenant,))
                cur.execute(
                    "INSERT INTO comptes_sessions (jeton, compte_id, cree_le, expire_le) "
                    "VALUES (%s, %s, %s, %s)",
                    (jeton, compte_id, maintenant, maintenant + duree_s))
            conn.commit()

    def session(self, jeton: str) -> Optional[Dict[str, Any]]:
        import time
        return self._executer(
            "SELECT * FROM comptes_sessions WHERE jeton = %s AND expire_le > %s",
            (jeton, time.time()), fetch="un")

    def supprimer_session(self, jeton: str) -> None:
        self._executer("DELETE FROM comptes_sessions WHERE jeton = %s", (jeton,))

    def supprimer_sessions_du_compte(self, compte_id: str) -> None:
        self._executer("DELETE FROM comptes_sessions WHERE compte_id = %s",
                       (compte_id,))

    # ── Exploitation ──
    def fermer(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def diagnostic(self) -> Dict[str, Any]:
        """État de la connexion, SANS le mot de passe du DSN.

        Sert au diagnostic depuis l'interface : personne ne doit pouvoir lire
        les identifiants de la base dans une réponse HTTP.
        """
        parties = urlsplit(self._dsn)
        infos = {
            "moteur": "postgresql",
            "hote": parties.hostname or "",
            "port": parties.port or 5432,
            "base": (parties.path or "/").lstrip("/"),
            "tls": "sslmode=disable" not in self._dsn,
        }
        try:
            infos["comptes"] = self.nombre()
            infos["joignable"] = True
        except Exception as e:
            infos["joignable"] = False
            infos["erreur"] = f"{type(e).__name__}: {e}"
        return infos
