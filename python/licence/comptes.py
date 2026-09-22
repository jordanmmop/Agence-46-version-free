"""Comptes utilisateurs : inscription, connexion, essai, abonnement.

STOCKAGE
--------
Table `comptes` de la base SQLite de l'application, via un DÉPÔT
(`DepotComptes`) qui isole complètement le reste du code du support de
stockage. Le jour où les comptes doivent vivre dans une base distante
(PostgreSQL, service d'authentification), il suffit de réécrire cette classe :
aucun appelant ne connaît SQLite, tous passent par les fonctions publiques de
ce module.

CE QUI N'EST PAS STOCKÉ ICI
---------------------------
AUCUNE donnée de carte bancaire. Ni numéro, ni date d'expiration, ni
cryptogramme, ni empreinte. Jamais, sous aucune forme.

La carte est saisie chez Stripe, sur ses pages, et n'atteint donc jamais ce
programme. Détenir ces données imposerait la conformité PCI-DSS et ferait
porter à l'éditeur le risque d'une fuite — pour un service que Stripe rend
déjà. L'application n'apprend du paiement que son RÉSULTAT.

MOT DE PASSE
------------
Jamais en clair : PBKDF2-HMAC-SHA256, 200 000 itérations, sel aléatoire de
16 octets par compte. Même schéma et même format que `utils/auth.py`, pour
qu'il n'existe qu'une seule façon de stocker un secret dans ce projet.

UNICITÉ
-------
Un e-mail ou un téléphone déjà enregistré ne peut pas resservir : c'est ce qui
empêche de recréer un compte pour s'offrir un nouvel essai de 3 jours une fois
le premier écoulé. La contrainte est portée par des index UNIQUE en base, donc
tenue par le moteur lui-même — pas seulement par une vérification applicative
qui laisserait passer deux inscriptions simultanées.
"""
import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

from licence import config as lconfig
from licence.etat import EtatLicence

logger = logging.getLogger(__name__)

_PBKDF2_ITER = 200_000
COOKIE_SESSION = "agence_compte"

_lock = threading.RLock()


# ═══════════════════════════ NORMALISATION / VALIDATION ═══════════════════

def normaliser_email(brut: str) -> str:
    """Forme canonique d'un e-mail : minuscules, sans espaces.

    C'est cette forme qui porte la contrainte d'unicité :
    « Jean.Dupont@Example.com » et « jean.dupont@example.com » sont le MÊME
    compte, sans quoi il suffirait d'une majuscule pour rouvrir un essai.
    """
    return (brut or "").strip().lower()


def normaliser_telephone(brut: str) -> str:
    """Forme AFFICHABLE d'un téléphone : chiffres et « + » de tête seulement.

    « 06 12 34 56 78 », « 06.12.34.56.78 » et « 0612345678 » deviennent la
    même chaîne. C'est la valeur qu'on réaffiche à l'utilisateur ; l'unicité,
    elle, se juge sur `cle_telephone()`.
    """
    brut = (brut or "").strip()
    if not brut:
        return ""
    plus = brut.startswith("+") or brut.startswith("00")
    chiffres = re.sub(r"\D", "", brut)
    if chiffres.startswith("00"):
        chiffres = chiffres[2:]
        plus = True
    return ("+" if plus else "") + chiffres


# Longueur du « numéro national significatif » retenu pour comparer deux
# lignes. 9 chiffres couvre la France (612345678) et la plupart des plans de
# numérotation européens.
_CLE_TEL_LONGUEUR = 9


def cle_telephone(brut: str) -> str:
    """Clé d'UNICITÉ d'un téléphone — insensible à la forme d'écriture.

    « +33 6 12 34 56 78 », « 0033612345678 » et « 06 12 34 56 78 » désignent
    LA MÊME LIGNE. Comparer les chaînes normalisées ne suffisait pas : elles
    donnent « +33612345678 » et « 0612345678 », deux valeurs différentes — et
    il suffisait donc d'écrire son numéro au format international pour se
    réinscrire et repartir pour trois jours d'essai.

    On compare donc les derniers chiffres significatifs, après avoir retiré
    l'indicatif pays éventuel et le zéro national de tête. Deux lignes de pays
    différents partageant ces neuf chiffres seraient confondues : c'est
    improbable, et le refus d'une inscription se lève par un message au
    support — l'inverse (un essai rouvert à volonté) ne se rattrape pas.
    """
    chiffres = re.sub(r"\D", "", brut or "")
    if not chiffres:
        return ""
    chiffres = chiffres.lstrip("0")            # zéro national ou « 00 » d'entête
    return chiffres[-_CLE_TEL_LONGUEUR:] if chiffres else ""


# Domaines où le point n'est pas significatif dans la partie locale : chez eux,
# « jean.dupont@ » et « jeandupont@ » sont la MÊME boîte.
_DOMAINES_SANS_POINT = {"gmail.com", "googlemail.com"}


def cle_email(brut: str) -> str:
    """Clé d'UNICITÉ d'un e-mail — insensible aux alias.

    « jean@example.com » et « jean+essai2@example.com » arrivent dans la même
    boîte : le suffixe « + » est un alias, pas une autre adresse. Sans ce
    traitement, un utilisateur rouvrait un essai de 3 jours autant de fois
    qu'il voulait, sans même changer de boîte mail.

    Le point est aussi retiré chez les fournisseurs où il n'est pas
    significatif — et uniquement chez eux : ailleurs, « j.dupont@ » et
    « jdupont@ » peuvent être deux personnes différentes.
    """
    email = normaliser_email(brut)
    if "@" not in email:
        return email
    local, _, domaine = email.rpartition("@")
    local = local.split("+", 1)[0]
    if domaine in _DOMAINES_SANS_POINT:
        local = local.replace(".", "")
    return f"{local}@{domaine}" if local else email


# Volontairement permissif : un validateur trop zélé refuse de vrais e-mails
# (sous-domaines, extensions longues, signes « + »). On vérifie la FORME —
# une adresse réellement valide se prouve par l'envoi d'un message, pas par
# une expression régulière.
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def _valider_inscription(donnees: Dict[str, Any]) -> Optional[str]:
    """Premier message d'erreur rencontré, ou None si tout est correct."""
    email = normaliser_email(donnees.get("email"))
    if not email or not _RE_EMAIL.match(email):
        return "Adresse e-mail invalide."
    if len(email) > 254:
        return "Adresse e-mail trop longue."

    mot = str(donnees.get("mot_de_passe") or "")
    if len(mot) < lconfig.MOT_DE_PASSE_MIN:
        return (f"Le mot de passe doit contenir au moins "
                f"{lconfig.MOT_DE_PASSE_MIN} caractères.")

    tel = normaliser_telephone(donnees.get("telephone"))
    # 8 chiffres : plus court que le plus court numéro national réel.
    if len(re.sub(r"\D", "", tel)) < 8:
        return "Numéro de téléphone invalide."

    for champ, libelle in (("adresse", "adresse postale"),
                           ("code_postal", "code postal"),
                           ("ville", "ville"),
                           ("pays", "pays")):
        if not str(donnees.get(champ) or "").strip():
            return f"Le champ « {libelle} » est obligatoire."

    # Refus EXPLICITE de toute donnée de carte : si un formulaire modifié ou
    # un appel direct en envoie, on ne l'enregistre pas « au cas où » — on
    # rejette la requête. La carte se saisit chez Stripe, nulle part ailleurs.
    for interdit in ("carte", "numero_carte", "card", "card_number", "cvc",
                     "cvv", "expiration", "exp_mois", "exp_annee", "iban"):
        if donnees.get(interdit):
            return ("Aucune donnée de carte bancaire ne doit être transmise à "
                    "l'application : le paiement se fait sur les pages "
                    "sécurisées de Stripe.")
    return None


# ═══════════════════════════ MOT DE PASSE ═════════════════════════════════

def hacher_mot_de_passe(mot: str, sel: bytes = None, iterations: int = None) -> str:
    """Hash au format « pbkdf2$<iter>$<sel_hex>$<clé_hex> » (cf. utils/auth)."""
    sel = sel or secrets.token_bytes(16)
    iterations = iterations or _PBKDF2_ITER
    cle = hashlib.pbkdf2_hmac("sha256", str(mot).encode(), sel, iterations)
    return f"pbkdf2${iterations}${sel.hex()}${cle.hex()}"


def verifier_mot_de_passe(mot: str, stocke: str) -> bool:
    """Comparaison à temps constant. Toute anomalie vaut échec."""
    try:
        marque, iter_s, sel_hex, _ = (stocke or "").split("$", 3)
        if marque != "pbkdf2":
            return False
        recalcule = hacher_mot_de_passe(str(mot), bytes.fromhex(sel_hex), int(iter_s))
        return hmac.compare_digest(recalcule, stocke)
    except Exception:
        return False


# ═══════════════════════════ DÉPÔT (stockage) ═════════════════════════════

class DepotComptes:
    """Accès au stockage des comptes. SEULE classe qui connaît SQLite.

    Remplacer ce dépôt par une implémentation distante ne demande de toucher à
    aucun autre fichier : les fonctions publiques du module l'utilisent
    exclusivement à travers les méthodes définies ici.
    """

    @contextmanager
    def _conn(self):
        """Connexion à la base, FERMÉE à coup sûr en sortie.

        `with sqlite3.connect(...)` valide ou annule la transaction mais ne
        FERME PAS la connexion : chaque appel laissait un descripteur ouvert,
        et le serveur finissait par ne plus pouvoir en ouvrir. Même discipline
        que `utils/database.Database._tx`, pour la même raison.

        DB_PATH est relu à CHAQUE connexion, et non capturé à l'import : la
        suite de tests redirige la base vers un dossier temporaire en cours
        d'exécution, et un chemin figé ferait écrire dans la vraie base de
        l'utilisateur.
        """
        from config import DB_PATH
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            # WAL : l'application lit et écrit en parallèle (agents, API,
            # auto-trader). Sans lui, un écrivain bloque tout le monde.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._creer_tables(conn)
            with conn:                      # commit / rollback automatique
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _creer_tables(conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS comptes (
                id              TEXT PRIMARY KEY,
                email           TEXT NOT NULL,
                telephone       TEXT NOT NULL,
                -- Clés de COMPARAISON (cf. cle_email / cle_telephone) : ce
                -- sont elles qui portent l'unicité, pas les valeurs affichées.
                -- « jean+x@ » et « +33 6… » ne doivent pas rouvrir un essai.
                email_cle       TEXT NOT NULL,
                telephone_cle   TEXT NOT NULL,
                mot_de_passe    TEXT NOT NULL,
                nom             TEXT DEFAULT '',
                adresse         TEXT DEFAULT '',
                code_postal     TEXT DEFAULT '',
                ville           TEXT DEFAULT '',
                pays            TEXT DEFAULT '',
                cree_le         REAL NOT NULL,
                essai_fin       REAL NOT NULL,
                etat            TEXT NOT NULL DEFAULT 'TRIAL',
                formule         TEXT DEFAULT '',
                abonne_jusqua   REAL,
                paiement_ref    TEXT DEFAULT '',
                derniere_connexion REAL
            );
            -- Unicité tenue par le MOTEUR : deux inscriptions simultanées avec
            -- le même e-mail ne peuvent pas passer toutes les deux.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_comptes_email_cle
                ON comptes(email_cle);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_comptes_telephone_cle
                ON comptes(telephone_cle);

            CREATE TABLE IF NOT EXISTS comptes_sessions (
                jeton     TEXT PRIMARY KEY,
                compte_id TEXT NOT NULL,
                cree_le   REAL NOT NULL,
                expire_le REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_compte
                ON comptes_sessions(compte_id);
        """)

    # ── Lectures ──
    def par_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Recherche par CLÉ : « jean+essai@ » retrouve le compte « jean@ »."""
        with self._conn() as c:
            r = c.execute("SELECT * FROM comptes WHERE email_cle = ?",
                          (cle_email(email),)).fetchone()
            return dict(r) if r else None

    def par_telephone(self, telephone: str) -> Optional[Dict[str, Any]]:
        """Recherche par CLÉ : « +33 6… » retrouve le compte « 06… »."""
        with self._conn() as c:
            r = c.execute("SELECT * FROM comptes WHERE telephone_cle = ?",
                          (cle_telephone(telephone),)).fetchone()
            return dict(r) if r else None

    def par_id(self, compte_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM comptes WHERE id = ?", (compte_id,)).fetchone()
            return dict(r) if r else None

    def nombre(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) n FROM comptes").fetchone()["n"])

    # ── Écritures ──
    def creer(self, compte: Dict[str, Any]) -> None:
        """Lève sqlite3.IntegrityError si l'e-mail ou le téléphone existe."""
        colonnes = ", ".join(compte)
        marques = ", ".join("?" * len(compte))
        with self._conn() as c:
            c.execute(f"INSERT INTO comptes ({colonnes}) VALUES ({marques})",
                      tuple(compte.values()))

    def modifier(self, compte_id: str, **champs) -> None:
        if not champs:
            return
        sets = ", ".join(f"{k} = ?" for k in champs)
        with self._conn() as c:
            c.execute(f"UPDATE comptes SET {sets} WHERE id = ?",
                      (*champs.values(), compte_id))

    # ── Sessions ──
    def creer_session(self, jeton: str, compte_id: str, duree_s: float) -> None:
        maintenant = time.time()
        with self._conn() as c:
            c.execute("DELETE FROM comptes_sessions WHERE expire_le < ?", (maintenant,))
            c.execute("INSERT INTO comptes_sessions (jeton, compte_id, cree_le, expire_le) "
                      "VALUES (?, ?, ?, ?)",
                      (jeton, compte_id, maintenant, maintenant + duree_s))

    def session(self, jeton: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM comptes_sessions WHERE jeton = ? AND expire_le > ?",
                          (jeton, time.time())).fetchone()
            return dict(r) if r else None

    def supprimer_session(self, jeton: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM comptes_sessions WHERE jeton = ?", (jeton,))

    def supprimer_sessions_du_compte(self, compte_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM comptes_sessions WHERE compte_id = ?", (compte_id,))


_depot = DepotComptes()


def depot() -> DepotComptes:
    return _depot


# ═══════════════════════════ CYCLE DE VIE DU COMPTE ═══════════════════════

def etat_du_compte(compte: Optional[Dict[str, Any]]) -> EtatLicence:
    """État RÉEL d'un compte, recalculé à chaque lecture depuis les dates.

    Recalculé, et non lu tel quel : la colonne `etat` n'est qu'un cache. Si
    l'essai s'est écoulé ou l'abonnement est arrivé à terme pendant que
    l'application tournait, l'état doit basculer SANS attendre une quelconque
    tâche de fond — sinon un compte resterait ouvert indéfiniment.
    """
    if not compte:
        return EtatLicence.COMPTE_REQUIS

    # Suspension prononcée par l'éditeur : elle prime sur tout le reste.
    if str(compte.get("etat") or "") == EtatLicence.SUSPENDU.value:
        return EtatLicence.SUSPENDU

    maintenant = time.time()
    abonne_jusqua = compte.get("abonne_jusqua") or 0
    if abonne_jusqua:
        if abonne_jusqua > maintenant:
            return EtatLicence.PRO_ACTIVE
        return EtatLicence.PRO_EXPIRED        # a payé, mais le terme est passé

    if (compte.get("essai_fin") or 0) > maintenant:
        return EtatLicence.TRIAL
    return EtatLicence.TRIAL_EXPIRED           # 3 jours écoulés, aucun paiement


def _synchroniser_etat(compte: Dict[str, Any]) -> EtatLicence:
    """Recalcule l'état et met la colonne à jour si elle a dérivé."""
    etat = etat_du_compte(compte)
    if str(compte.get("etat") or "") != etat.value:
        try:
            _depot.modifier(compte["id"], etat=etat.value)
            compte["etat"] = etat.value
        except Exception as e:
            logger.warning("[comptes] État non persisté : %s", e)
    return etat


def jours_essai_restants(compte: Optional[Dict[str, Any]]) -> float:
    if not compte:
        return 0.0
    return max(0.0, ((compte.get("essai_fin") or 0) - time.time()) / 86400.0)


def public(compte: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Vue du compte destinée à l'interface.

    Ne sort JAMAIS le hash du mot de passe ni aucun jeton de session.
    L'adresse postale complète non plus : l'interface n'en a pas besoin pour
    afficher un en-tête, et une donnée qu'on n'envoie pas ne fuite pas.
    """
    if not compte:
        return {}
    etat = etat_du_compte(compte)
    return {
        "id": compte.get("id"),
        "email": compte.get("email"),
        "telephone": compte.get("telephone"),
        "ville": compte.get("ville"),
        "pays": compte.get("pays"),
        "cree_le": compte.get("cree_le"),
        "etat": etat.value,
        "libelle": etat.libelle,
        "essai_fin": compte.get("essai_fin"),
        "essai_jours_restants": round(jours_essai_restants(compte), 2),
        "formule": compte.get("formule") or "",
        "abonne_jusqua": compte.get("abonne_jusqua"),
        "abonne": etat.est_pro,
    }


# ═══════════════════════════ INSCRIPTION / CONNEXION ══════════════════════

class ErreurCompte(Exception):
    """Refus fonctionnel (donnée invalide, compte existant, identifiants faux).

    `code` permet à l'interface de réagir autrement qu'en affichant le texte :
    « compte_existant » renvoie vers la connexion, « identifiants » non.
    """

    def __init__(self, message: str, code: str = "compte"):
        self.code = code
        super().__init__(message)


def inscrire(donnees: Dict[str, Any]) -> Dict[str, Any]:
    """Crée un compte et ouvre l'essai de 3 jours. Lève ErreurCompte.

    Un e-mail ou un téléphone DÉJÀ ENREGISTRÉ est refusé : c'est ce qui
    empêche de repartir pour trois jours d'essai à chaque expiration.
    """
    erreur = _valider_inscription(donnees)
    if erreur:
        raise ErreurCompte(erreur, "validation")

    email = normaliser_email(donnees.get("email"))
    telephone = normaliser_telephone(donnees.get("telephone"))

    with _lock:
        if _depot.par_email(email):
            raise ErreurCompte(
                "Un compte existe déjà avec cette adresse e-mail. "
                "Connectez-vous ou réinitialisez votre mot de passe.",
                "compte_existant")
        if _depot.par_telephone(telephone):
            raise ErreurCompte(
                "Un compte existe déjà avec ce numéro de téléphone.",
                "compte_existant")

        maintenant = time.time()
        compte = {
            "id": secrets.token_hex(16),
            "email": email,
            "telephone": telephone,
            "email_cle": cle_email(email),
            "telephone_cle": cle_telephone(telephone),
            "mot_de_passe": hacher_mot_de_passe(donnees["mot_de_passe"]),
            "nom": _texte(donnees.get("nom"), 120),
            "adresse": _texte(donnees.get("adresse"), 200),
            "code_postal": _texte(donnees.get("code_postal"), 20),
            "ville": _texte(donnees.get("ville"), 120),
            "pays": _texte(donnees.get("pays"), 80),
            "cree_le": maintenant,
            "essai_fin": maintenant + lconfig.TRIAL_DUREE_JOURS * 86400,
            "etat": EtatLicence.TRIAL.value,
            "formule": "",
            "abonne_jusqua": None,
            "paiement_ref": "",
            "derniere_connexion": maintenant,
        }
        try:
            _depot.creer(compte)
        except sqlite3.IntegrityError:
            # Deux inscriptions simultanées : l'index UNIQUE a tranché. Le
            # perdant reçoit le même message que s'il avait été second.
            raise ErreurCompte(
                "Un compte existe déjà avec ces informations de contact.",
                "compte_existant")
    logger.info("[comptes] Nouveau compte %s — essai de %d jours",
                email, lconfig.TRIAL_DUREE_JOURS)
    return compte


def _texte(valeur, maximum: int) -> str:
    """Champ libre assaini : espaces normalisés, longueur bornée, caractères
    de contrôle retirés (ils n'ont rien à faire dans une adresse et brouillent
    les journaux)."""
    brut = str(valeur or "").strip()
    brut = "".join(c for c in brut if unicodedata.category(c)[0] != "C")
    return " ".join(brut.split())[:maximum]


def connecter(email: str, mot_de_passe: str) -> Dict[str, Any]:
    """Vérifie les identifiants et renvoie le compte. Lève ErreurCompte.

    Le message est le MÊME que l'e-mail soit inconnu ou le mot de passe faux :
    distinguer les deux dirait à un inconnu quelles adresses sont inscrites.
    """
    compte = _depot.par_email(normaliser_email(email))
    if not compte or not verifier_mot_de_passe(mot_de_passe, compte.get("mot_de_passe", "")):
        raise ErreurCompte("E-mail ou mot de passe incorrect.", "identifiants")
    _depot.modifier(compte["id"], derniere_connexion=time.time())
    _synchroniser_etat(compte)
    return compte


def ouvrir_session(compte_id: str) -> str:
    """Crée une session et renvoie son jeton (valeur du cookie).

    Jeton ALÉATOIRE de 32 octets conservé en base, et non un jeton signé
    autoporteur : une session doit pouvoir être RÉVOQUÉE immédiatement —
    à la déconnexion, au changement de mot de passe, à la suspension du
    compte. Un jeton signé resterait valable jusqu'à sa date d'expiration.
    """
    jeton = secrets.token_urlsafe(32)
    _depot.creer_session(jeton, compte_id, lconfig.SESSION_DUREE_JOURS * 86400)
    return jeton


def fermer_session(jeton: str) -> None:
    if jeton:
        _depot.supprimer_session(jeton)


def compte_de_session(jeton: str) -> Optional[Dict[str, Any]]:
    """Compte rattaché à un jeton de session valide, sinon None."""
    if not jeton:
        return None
    session = _depot.session(jeton)
    if not session:
        return None
    compte = _depot.par_id(session["compte_id"])
    if compte:
        _synchroniser_etat(compte)
    return compte


# ═══════════════════════════ ABONNEMENT ═══════════════════════════════════

def activer_abonnement(compte_id: str, formule: str, reference: str,
                       duree_jours: Optional[float] = None) -> Dict[str, Any]:
    """Ouvre les droits Pro d'un compte APRÈS confirmation d'un paiement.

    N'est appelée QUE par `licence/stripe_paiement.py`, et seulement quand
    Stripe a confirmé l'encaissement — jamais depuis une route que le
    navigateur pourrait appeler directement. `reference` est l'identifiant
    Stripe de la session ou de la facture : il rend le paiement traçable et
    permet de rejouer la vérification.
    """
    formule = str(formule or lconfig.FORMULE_DEFAUT)
    if formule not in lconfig.FORMULES:
        formule = lconfig.FORMULE_DEFAUT
    if duree_jours is None:
        duree_jours = lconfig.DUREE_DROITS_JOURS.get(formule, 31)

    compte = _depot.par_id(compte_id)
    if not compte:
        raise ErreurCompte("Compte introuvable.", "compte_introuvable")

    # Un renouvellement PROLONGE le terme en cours au lieu de le remplacer :
    # payer en avance ne doit pas faire perdre les jours déjà acquis.
    base = max(time.time(), compte.get("abonne_jusqua") or 0)
    jusqua = base + duree_jours * 86400

    _depot.modifier(compte_id, formule=formule, abonne_jusqua=jusqua,
                    paiement_ref=str(reference or "")[:200],
                    etat=EtatLicence.PRO_ACTIVE.value)
    logger.info("[comptes] Abonnement %s activé pour %s jusqu'au %s",
                formule, compte.get("email"),
                time.strftime("%Y-%m-%d", time.localtime(jusqua)))
    return _depot.par_id(compte_id)


def suspendre(compte_id: str, raison: str = "") -> None:
    """Suspend un compte et FERME ses sessions ouvertes.

    Fermer les sessions est le point important : sans cela, un compte suspendu
    resterait utilisable dans l'onglet déjà ouvert jusqu'à sa déconnexion.
    """
    _depot.modifier(compte_id, etat=EtatLicence.SUSPENDU.value)
    _depot.supprimer_sessions_du_compte(compte_id)
    logger.info("[comptes] Compte %s suspendu%s", compte_id,
                f" ({raison})" if raison else "")


def contact_deja_utilise(email: str = "", telephone: str = "") -> bool:
    """Ce contact est-il déjà rattaché à un compte ?

    Sert à l'interface pour prévenir AVANT la soumission du formulaire. La
    décision, elle, reste prise par `inscrire()` et par les index UNIQUE :
    une vérification préalable n'est qu'un confort d'affichage.
    """
    if email and _depot.par_email(normaliser_email(email)):
        return True
    if telephone and _depot.par_telephone(normaliser_telephone(telephone)):
        return True
    return False


# ═══════════════════════════ COMPTE DE LA REQUÊTE EN COURS ════════════════
# Le reste de l'application (feature gate, quotas, orchestrateur) doit savoir
# QUI agit, sans se passer le compte de fonction en fonction sur toute la
# pile. Un `ContextVar` le porte pour la durée d'une requête : il est isolé
# entre requêtes concurrentes, et `asyncio.to_thread` le recopie — donc un
# cycle d'analyse déporté dans un thread voit bien le compte appelant.
#
# Ce n'est PAS une variable globale d'authentification : elle est posée par le
# middleware à partir d'un cookie de session vérifié en base, et remise à zéro
# à la fin de la requête. Un thread de fond qui n'a pas de requête associée
# (boucle de l'auto-trader) n'y trouve rien — et doit donc nommer
# explicitement le compte qu'il sert.
from contextvars import ContextVar

_compte_courant: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "agence_compte_courant", default=None)


def definir_compte_courant(compte: Optional[Dict[str, Any]]):
    """Pose le compte de la requête. Renvoie le jeton de remise à zéro."""
    return _compte_courant.set(compte)


def reinitialiser_compte_courant(jeton) -> None:
    try:
        _compte_courant.reset(jeton)
    except (ValueError, LookupError):
        # Le jeton vient d'un autre contexte (tâche annulée, thread distinct) :
        # sans ce garde, la fin de requête lèverait une exception qui
        # masquerait la vraie réponse.
        _compte_courant.set(None)


def compte_courant() -> Optional[Dict[str, Any]]:
    """Compte de la requête en cours, ou None si personne n'est connecté."""
    return _compte_courant.get()


def etat_courant() -> EtatLicence:
    """État du compte de la requête en cours (COMPTE_REQUIS si aucun)."""
    return etat_du_compte(compte_courant())


# ═══════════════════════════ ANTI-FORCE-BRUTE ═════════════════════════════
# Le formulaire de connexion est exposé sur le réseau local (l'application
# écoute sur 0.0.0.0 pour l'accès depuis un téléphone). Sans limite, un mot de
# passe se devine hors ligne à la vitesse du réseau.
#
# Compteurs PAR IP SOURCE, jamais globaux : un compteur global permettrait à
# n'importe qui de verrouiller le propriétaire légitime en épuisant
# volontairement les essais. Même schéma que `utils/auth.py`.
_MAX_ECHECS = 5
_BLOCAGE_S = 30
_echecs: Dict[str, int] = {}
_bloque_jusqua: Dict[str, float] = {}
_brute_lock = threading.Lock()


def connexion_bloquee(ip) -> Tuple[bool, int]:
    """(bloqué, secondes restantes) pour cette IP."""
    with _brute_lock:
        restant = _bloque_jusqua.get(str(ip or "?"), 0.0) - time.time()
        return (restant > 0, max(0, int(restant)))


def enregistrer_echec(ip) -> None:
    """Blocage à durée croissante : 30 s, 60 s, 120 s… plafonné à une heure."""
    cle = str(ip or "?")
    with _brute_lock:
        n = _echecs.get(cle, 0) + 1
        _echecs[cle] = n
        if n >= _MAX_ECHECS:
            _bloque_jusqua[cle] = time.time() + min(
                _BLOCAGE_S * (2 ** (n - _MAX_ECHECS)), 3600)
        # Borne mémoire : purger les entrées dont le blocage est levé.
        if len(_echecs) > 512:
            maintenant = time.time()
            for k in [k for k, t in _bloque_jusqua.items() if t < maintenant]:
                _echecs.pop(k, None)
                _bloque_jusqua.pop(k, None)
            if len(_echecs) > 512:
                for k in [k for k in _echecs if k not in _bloque_jusqua]:
                    _echecs.pop(k, None)


def reinitialiser_echecs(ip=None) -> None:
    with _brute_lock:
        if ip is None:
            _echecs.clear()
            _bloque_jusqua.clear()
        else:
            cle = str(ip or "?")
            _echecs.pop(cle, None)
            _bloque_jusqua.pop(cle, None)
