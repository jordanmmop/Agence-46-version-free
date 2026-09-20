import sqlite3
import json
import numpy as np
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from config import DB_PATH


import math


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)):
            f = float(obj)
            return f if math.isfinite(f) else None   # inf/NaN → JSON invalide
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, (np.bool_,)): return bool(obj)
        if isinstance(obj, datetime): return obj.isoformat()
        return super().default(obj)


def _dumps(obj) -> str:
    """json.dumps sûr : pré-nettoie numpy + inf/NaN (→ None) via json_safe pour
    ne jamais écrire de littéral JSON invalide ('Infinity'/'NaN') en base."""
    try:
        from models.json_safe import json_safe
        obj = json_safe(obj)
    except Exception:
        pass
    return json.dumps(obj, cls=_Encoder)


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL : les 46 agents écrivent en parallèle (ThreadPoolExecutor) pendant
        # que l'API lit — sans WAL, un écrivain bloque tout et lève
        # "database is locked" sous charge.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _tx(self):
        """Transaction avec fermeture garantie de la connexion.

        `with sqlite3.connect(...)` commit/rollback mais ne ferme PAS la
        connexion — on la ferme explicitement ici.
        """
        conn = self._conn()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._tx() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS signaux (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    agent_nom TEXT NOT NULL,
                    symbole TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confiance REAL,
                    prix_entree REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    raisonnement TEXT,
                    donnees TEXT,
                    timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_signaux_ts  ON signaux(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_signaux_sym ON signaux(symbole);
                CREATE INDEX IF NOT EXISTS idx_signaux_agt ON signaux(agent_id);

                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    symbole TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    quantite REAL,
                    prix_entree REAL,
                    prix_sortie REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    statut TEXT,
                    pnl REAL DEFAULT 0,
                    pnl_pct REAL DEFAULT 0,
                    commission REAL DEFAULT 0,
                    agent_source TEXT,
                    ouvert_le TEXT,
                    ferme_le TEXT
                );
                CREATE TABLE IF NOT EXISTS portfolio_historique (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    valeur_totale REAL,
                    capital_disponible REAL,
                    pnl_total REAL,
                    pnl_pct REAL,
                    nb_positions INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_portfolio_ts ON portfolio_historique(timestamp DESC);

                CREATE TABLE IF NOT EXISTS agents_activite (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    agent_nom TEXT NOT NULL,
                    symbole TEXT,
                    action TEXT,
                    confiance REAL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ordres_executes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    horodatage TEXT NOT NULL,
                    symbole TEXT,
                    action TEXT,
                    volume REAL,
                    prix REAL,
                    ticket TEXT,
                    succes INTEGER,
                    erreur TEXT,
                    mode TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ordres_ts ON ordres_executes(id DESC);
            """)
        self._migrer()

    # Migrations de schéma : `CREATE TABLE IF NOT EXISTS` n'ajoute JAMAIS une
    # colonne à une table déjà créée par une version antérieure. Sans ce
    # mécanisme, toute nouvelle colonne ferait planter (« no such column ») les
    # bases des utilisateurs qui ont déjà de l'historique réel.
    # Pour ajouter une migration : append une instruction ALTER/CREATE ici.
    _MIGRATIONS = [
        # v1 → v2 : exemple de forme attendue (aucune migration en attente)
        # "ALTER TABLE ordres_executes ADD COLUMN commentaire TEXT",
    ]

    def _migrer(self) -> None:
        try:
            with self._tx() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0] or 0
                cible = len(self._MIGRATIONS)
                if version >= cible:
                    return
                for i in range(version, cible):
                    sql = self._MIGRATIONS[i]
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError as e:
                        # colonne déjà présente (base créée après coup) → on passe
                        if "duplicate column" not in str(e).lower():
                            raise
                conn.execute(f"PRAGMA user_version = {cible}")
                import logging
                logging.getLogger(__name__).info(
                    f"[DB] Schéma migré de la version {version} à {cible}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[DB] Migration de schéma échouée : {e}")

    def sauver_signal(self, signal_dict: Dict) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                """INSERT INTO signaux
                   (agent_id, agent_nom, symbole, action, confiance, prix_entree,
                    stop_loss, take_profit, raisonnement, donnees, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_dict.get("agent_id"), signal_dict.get("agent_nom"),
                    signal_dict.get("symbole"), signal_dict.get("action"),
                    signal_dict.get("confiance"), signal_dict.get("prix_entree"),
                    signal_dict.get("stop_loss"), signal_dict.get("take_profit"),
                    signal_dict.get("raisonnement"),
                    _dumps(signal_dict.get("donnees", {})),
                    signal_dict.get("timestamp", datetime.now().isoformat()),
                )
            )
            return cur.lastrowid

    def lire_signaux(self, limite: int = 100, symbole: str = None) -> List[Dict]:
        with self._tx() as conn:
            if symbole:
                rows = conn.execute(
                    "SELECT * FROM signaux WHERE symbole=? ORDER BY timestamp DESC LIMIT ?",
                    (symbole, limite)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM signaux ORDER BY timestamp DESC LIMIT ?", (limite,)
                ).fetchall()
            return [dict(r) for r in rows]

    def sauver_portfolio(self, portfolio_dict: Dict) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO portfolio_historique
                   (timestamp, valeur_totale, capital_disponible, pnl_total, pnl_pct, nb_positions)
                   VALUES (?,?,?,?,?,?)""",
                (
                    datetime.now().isoformat(),
                    portfolio_dict.get("valeur_totale"),
                    portfolio_dict.get("capital_disponible"),
                    portfolio_dict.get("pnl_total"),
                    portfolio_dict.get("pnl_total_pct"),
                    portfolio_dict.get("nb_positions"),
                )
            )

    def lire_portfolio_historique(self, limite: int = 500) -> List[Dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio_historique ORDER BY timestamp DESC LIMIT ?", (limite,)
            ).fetchall()
            return [dict(r) for r in rows]

    def nettoyer_anciens_signaux(self, jours_retention: int = 30) -> int:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=jours_retention)).isoformat()
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM signaux WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM portfolio_historique WHERE timestamp < ?", (cutoff,))
            supprimes = cur.rowcount
        # VACUUM interdit dans une transaction : connexion dédiée en autocommit.
        # Sous écritures WAL concurrentes (46 agents), il peut lever « database
        # is locked » : on configure un busy_timeout et on tolère l'échec (le
        # nettoyage des lignes a déjà eu lieu ; le VACUUM n'est qu'un compactage).
        vac = sqlite3.connect(str(self.path), timeout=10)
        try:
            vac.isolation_level = None
            vac.execute("PRAGMA busy_timeout=5000")
            vac.execute("VACUUM")
        except sqlite3.OperationalError as e:
            import logging
            logging.getLogger(__name__).warning(f"[DB] VACUUM ignoré : {e}")
        finally:
            vac.close()
        return supprimes

    def sauver_ordre(self, entry: Dict) -> None:
        """Persiste un ordre exécuté (journal MT5) pour l'historique."""
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO ordres_executes
                   (horodatage, symbole, action, volume, prix, ticket, succes, erreur, mode)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    # ISO complet (date+heure) : nécessaire au rapport quotidien
                    datetime.now().isoformat(timespec="seconds"),
                    entry.get("symbol"), entry.get("action"),
                    entry.get("volume"), entry.get("price"),
                    str(entry.get("ticket")) if entry.get("ticket") is not None else None,
                    1 if entry.get("success") else 0,
                    entry.get("error"),
                    "simulation" if entry.get("simulated") else "reel",
                )
            )

    def ordres_du_jour(self, jour_iso: str) -> List[Dict]:
        """Ordres d'une journée (jour_iso = 'YYYY-MM-DD') pour le rapport."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM ordres_executes WHERE horodatage LIKE ? ORDER BY id",
                (jour_iso + "%",)
            ).fetchall()
            return [dict(r) for r in rows]

    def lire_ordres(self, limite: int = 100) -> List[Dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM ordres_executes ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()
            return [dict(r) for r in rows]

    def stats_ordres(self) -> Dict:
        """Statistiques d'exécution : total, réussis, échoués, par mode."""
        with self._tx() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN succes=1 THEN 1 ELSE 0 END) as reussis,
                          SUM(CASE WHEN mode='reel' THEN 1 ELSE 0 END) as reels
                   FROM ordres_executes"""
            ).fetchone()
            d = dict(row) if row else {}
            # SUM() renvoie NULL sur une table vide → forcer des entiers
            return {
                "total":   d.get("total") or 0,
                "reussis": d.get("reussis") or 0,
                "reels":   d.get("reels") or 0,
            }

    def lire_stats_agents(self) -> List[Dict]:
        with self._tx() as conn:
            rows = conn.execute("""
                SELECT agent_id, agent_nom,
                       COUNT(*) as nb_signaux,
                       AVG(confiance) as confiance_moy,
                       MAX(timestamp) as derniere_activite
                FROM signaux GROUP BY agent_id
                ORDER BY nb_signaux DESC
            """).fetchall()
            return [dict(r) for r in rows]
