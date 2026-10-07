"""LicenseManager — toute la logique des licences Agence Novia, ici et nulle part ailleurs.

RESPONSABILITÉS
---------------
activation · validation périodique · vérification de signature · stockage
local · expiration · mode hors ligne · changement de licence · désactivation.

Le reste de l'application n'en connaît qu'une chose : `manager().etat()`, lu
par `licence/abonnement.py`, le point de décision unique du feature gate.

CE QUI EST STOCKÉ, ET OÙ
------------------------
Dans ~/.agence_financiere/config.json (mode 600), sous deux clés :

    novia_installation_id   UUID4 aléatoire, tiré une fois. Conservé à la
                            désactivation : réactiver le même poste ne doit
                            pas consommer un second emplacement d'appareil.
    novia_licence           clé, jeton signé, dates de validation, dernier
                            statut serveur. SEULE cette clé est effacée à la
                            désactivation — jamais un projet, jamais une donnée.

LE TEMPS
--------
Trois horloges, une règle : on ne recule jamais.

    maintenant effectif = max(horloge locale, plus grande heure déjà vue,
                              date d'émission signée du jeton)

Reculer l'horloge de l'ordinateur ne rend donc aucun jour hors ligne. Le seul
moyen de faire redescendre « la plus grande heure déjà vue » est une
validation EN LIGNE réussie avec une horloge d'accord avec le serveur — ce qui
permet à quelqu'un dont l'horloge était en avance de s'en sortir une fois
corrigée, sans offrir de porte à qui voudrait remonter le temps.

LIMITE ASSUMÉE
--------------
Hors ligne, rien ne peut empêcher quelqu'un qui contrôle sa machine de
restaurer une ancienne copie de config.json. Ce qu'il récupère au pire : un
jeton qui expire à sa date signée `offline_until`, bornée par
OFFLINE_GRACE_PERIOD. C'est la limite de toute licence hors ligne ; elle est
écrite ici plutôt que passée sous silence.
"""
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

from licence import config as lconfig
from licence import jeton_novia
from licence.license_state import (MESSAGES, MESSAGES_RAISON, LicenseState,
                                   LicenseTier, Raison)
from licence.novia_client import (CertificatRefuse, ConfigurationInvalide,
                                  ErreurNovia, NoviaClient, PasDeConnexion,
                                  ReponseInvalide, ServeurIndisponible)

CLE_INSTALLATION = "novia_installation_id"
CLE_STOCKAGE = "novia_licence"

JOUR = 86400
# Délai minimal entre deux tentatives de revalidation échouées : hors ligne,
# on ne martèle pas le réseau à chaque requête de l'interface.
DELAI_NOUVEL_ESSAI_S = 6 * 3600
# En deçà, la revalidation devient due même avant LICENSE_CHECK_INTERVAL :
# une licence ne doit pas s'éteindre faute d'avoir été revérifiée à temps.
MARGE_AVANT_FIN_HORS_LIGNE_S = 2 * JOUR
# Refus de Novia qu'un PAIEMENT peut lever : licence expirée (essai fini,
# abonnement échu) ou suspendue (impayé). Ils sont retentés toutes les
# DELAI_NOUVEL_ESSAI_S : sans cela, un client qui renouvelle — ou qui achète
# après la fin de son essai — resterait bloqué tant qu'il ne pense pas à
# cliquer « Actualiser la licence ». Une licence révoquée ou une clé invalide
# ne se rattrapent pas par un paiement : elles ne sont pas retentées.
ETATS_RATTRAPABLES = ("EXPIRED", "SUSPENDED")
# Décalage entre horloge locale et heure signée au-delà duquel on prévient.
DECALAGE_SIGNALE_S = JOUR
# L'heure vue n'est réécrite sur disque que si elle a avancé d'au moins ceci.
PAS_ECRITURE_HEURE_S = 600

_MOTIF_CLE = re.compile(r"^NOVIA(-[A-Z0-9]{4}){3,6}$")

journal = logging.getLogger("agence.licence")


# ═══════════════════════════ OUTILS ═══════════════════════════════════════

def normaliser_cle(brute: str) -> str:
    """Majuscules, sans espaces. Chaîne vide si ce n'est pas une clé Novia."""
    cle = re.sub(r"\s+", "", str(brute or "")).upper()
    return cle if _MOTIF_CLE.match(cle) else ""


def masquer_cle(cle: str) -> str:
    """« NOVIA-ABCD-EFGH-IJKL-1234 » → « NOVIA-****-****-****-1234 ».

    Seule forme autorisée dans un journal ou à l'écran. Le dernier groupe
    suffit à l'utilisateur pour reconnaître SA clé, pas à un tiers pour
    l'utiliser.
    """
    cle = str(cle or "")
    morceaux = cle.split("-")
    if len(morceaux) < 3:
        return "NOVIA-****" if cle else ""
    return "-".join([morceaux[0]] + ["****"] * (len(morceaux) - 2) + [morceaux[-1]])


def _date(horodatage: Optional[float]) -> str:
    if not horodatage:
        return ""
    return datetime.fromtimestamp(float(horodatage)).strftime("%d/%m/%Y")


def _version_app() -> str:
    try:
        from utils.version import version
        return version()
    except Exception:
        return "0.0.0-dev"


_journal_pret = False


def _preparer_journal() -> None:
    """Journal local dédié : ~/.agence_financiere/logs/licence.log.

    Rotation à 1 Mo × 3 : un poste resté des mois hors ligne ne doit pas
    remplir le disque de tentatives échouées.
    """
    global _journal_pret
    if _journal_pret:
        return
    _journal_pret = True
    try:
        from utils import app_config
        dossier = app_config._PATH.parent / "logs"
        dossier.mkdir(parents=True, exist_ok=True)
        gestionnaire = RotatingFileHandler(dossier / "licence.log",
                                           maxBytes=1_000_000, backupCount=3,
                                           encoding="utf-8")
        gestionnaire.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        journal.addHandler(gestionnaire)
        journal.setLevel(logging.INFO)
    except Exception as e:                 # disque plein, droits : on continue
        logging.getLogger(__name__).warning("[licence] Journal local indisponible : %s", e)


# ═══════════════════════════ LE GESTIONNAIRE ══════════════════════════════

class LicenseManager:
    """Licences Novia d'un produit. Générique : rien n'y est propre à Agence 46."""

    def __init__(self, client: Optional[NoviaClient] = None,
                 produit: Optional[str] = None,
                 cles_publiques: Optional[Dict[str, str]] = None,
                 horloge=time.time):
        self.produit = produit or lconfig.novia_produit()
        self._cles = cles_publiques
        self.client = client or NoviaClient(lconfig.novia_api_url(), self.produit)
        self._horloge = horloge
        self._verrou = threading.RLock()
        self._memo_jeton: Optional[tuple] = None     # (jeton, revendications)
        _preparer_journal()

    # ── Configuration ──
    def cles_publiques(self) -> Dict[str, str]:
        return self._cles if self._cles is not None else lconfig.novia_cles_publiques()

    def configure(self) -> bool:
        return bool(self.client.url_api and self.cles_publiques())

    # ── Stockage ──
    def installation_id(self) -> str:
        """UUID4 aléatoire, créé au premier appel et conservé.

        Aucune donnée matérielle ni personnelle : réinstaller l'application en
        conservant son dossier utilisateur garde le même identifiant, donc le
        même emplacement d'appareil côté Novia.
        """
        from utils import app_config
        with self._verrou:
            existant = app_config.get(CLE_INSTALLATION)
            if existant:
                try:
                    return str(uuid.UUID(str(existant)))
                except ValueError:
                    journal.warning("Identifiant d'installation illisible : remplacé.")
            nouveau = str(uuid.uuid4())
            app_config.set(CLE_INSTALLATION, nouveau)
            return nouveau

    def _lire(self) -> Dict[str, Any]:
        from utils import app_config
        donnees = app_config.get(CLE_STOCKAGE) or {}
        return dict(donnees) if isinstance(donnees, dict) else {}

    def _ecrire(self, donnees: Dict[str, Any]) -> None:
        from utils import app_config
        app_config.set(CLE_STOCKAGE, donnees)

    def _effacer(self) -> None:
        """Retire l'activation locale. SEULEMENT elle."""
        from utils import app_config
        app_config.mutate(lambda cfg: cfg.pop(CLE_STOCKAGE, None))
        self._memo_jeton = None

    # ── Le temps ──
    def _maintenant(self, donnees: Dict[str, Any]) -> float:
        """Heure effective : on ne revient jamais en arrière."""
        return max(float(self._horloge()), float(donnees.get("heure_vue") or 0))

    def _noter_heure(self, donnees: Dict[str, Any]) -> None:
        """Retient la plus grande heure vue — écrite avec parcimonie."""
        locale = float(self._horloge())
        vue = float(donnees.get("heure_vue") or 0)
        if locale < vue - lconfig.NOVIA_RECUL_HORLOGE_SUSPECT_S:
            if not donnees.get("recul_signale"):
                journal.warning("Horloge reculée de %.1f jour(s) par rapport à la "
                                "dernière heure connue : ignorée.",
                                (vue - locale) / JOUR)
                donnees["recul_signale"] = True
                self._ecrire(donnees)
            return
        if locale - vue >= PAS_ECRITURE_HEURE_S:
            donnees["heure_vue"] = locale
            donnees.pop("recul_signale", None)
            self._ecrire(donnees)

    # ── Jeton ──
    def _verifier_jeton(self, jeton: str, maintenant: float) -> Dict[str, Any]:
        """Signature et revendications. Mémoïsé : l'état est lu à chaque
        requête HTTP, et un même jeton ne change pas d'authenticité."""
        memo = self._memo_jeton
        if memo and memo[0] == jeton:
            revendications = memo[1]
        else:
            revendications = jeton_novia.verifier(
                jeton, self.cles_publiques(), self.produit, self.installation_id(),
                maintenant, _version_app(), tolerance_s=JOUR)
            self._memo_jeton = (jeton, revendications)
        return revendications

    def _fin_hors_ligne(self, rev: Dict[str, Any]) -> float:
        """Limite hors ligne : celle signée, RACCOURCIE par la configuration
        locale si besoin — jamais allongée."""
        grace = float(lconfig.OFFLINE_GRACE_PERIOD_JOURS) * JOUR
        fin = min(float(rev["offline_until"]), float(rev["iat"]) + grace)
        if rev.get("exp"):
            fin = min(fin, float(rev["exp"]))
        return fin

    # ═══════════════════════ ÉTAT (100 % local) ═══════════════════════════

    def etat(self) -> Dict[str, Any]:
        """État courant. AUCUN accès réseau : appelé à chaque requête."""
        with self._verrou:
            if not self.configure():
                return self._resultat(LicenseState.NOT_ACTIVATED, Raison.NON_CONFIGURE, {})
            donnees = self._lire()
            if not donnees.get("cle"):
                return self._resultat(LicenseState.NOT_ACTIVATED, Raison.AUCUNE, donnees)

            # Un refus EXPLICITE du serveur prime sur un jeton encore en vie :
            # une licence révoquée ne doit pas survivre jusqu'à son échéance
            # hors ligne. Le jeton a d'ailleurs été effacé à ce moment-là.
            statut_serveur = str(donnees.get("statut_serveur") or "")
            if statut_serveur in ("REVOKED", "SUSPENDED"):
                return self._resultat(LicenseState(statut_serveur), Raison.AUCUNE, donnees)
            if statut_serveur == "EXPIRED":
                return self._resultat(LicenseState.EXPIRED, Raison.LICENCE_ECHUE, donnees)
            if statut_serveur == "INVALID":
                return self._resultat(LicenseState.INVALID, Raison(
                    donnees.get("raison") or Raison.CLE_INCONNUE.value), donnees)

            jeton = donnees.get("jeton") or ""
            if not jeton:
                return self._resultat(LicenseState.NOT_ACTIVATED, Raison.AUCUNE, donnees)

            self._noter_heure(donnees)
            maintenant = self._maintenant(donnees)
            try:
                rev = self._verifier_jeton(jeton, maintenant)
            except jeton_novia.JetonRefuse as e:
                journal.warning("Licence locale refusée (%s) : %s",
                                masquer_cle(donnees.get("cle")), e)
                return self._resultat(LicenseState.INVALID, Raison(e.raison), donnees)

            maintenant = max(maintenant, float(rev["iat"]))
            tol = lconfig.NOVIA_TOLERANCE_HORLOGE_S
            if rev.get("exp") and maintenant > float(rev["exp"]) + tol:
                return self._resultat(LicenseState.EXPIRED, Raison.LICENCE_ECHUE,
                                      donnees, rev)
            if maintenant > self._fin_hors_ligne(rev) + tol:
                return self._resultat(LicenseState.EXPIRED, Raison.HORS_LIGNE_EPUISE,
                                      donnees, rev)

            statut_jeton = str(rev.get("status") or "ACTIVE").upper()
            if statut_jeton != "ACTIVE":
                return self._resultat(LicenseState.depuis(statut_jeton),
                                      Raison.AUCUNE, donnees, rev)

            recente = (maintenant - float(donnees.get("derniere_validation") or 0)
                       <= float(lconfig.LICENSE_CHECK_INTERVAL_JOURS) * JOUR)
            return self._resultat(LicenseState.ACTIVE if recente
                                  else LicenseState.OFFLINE_VALID,
                                  Raison.AUCUNE, donnees, rev)

    def _resultat(self, etat: LicenseState, raison: Raison, donnees: Dict[str, Any],
                  rev: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rev = rev or {}
        niveau = LicenseTier.depuis(rev.get("tier") if rev else donnees.get("niveau"))
        message = MESSAGES_RAISON.get(raison) or MESSAGES[etat]
        fin_hors_ligne = self._fin_hors_ligne(rev) if rev else None
        locale = float(self._horloge())
        return {
            "etat": etat.value,
            "raison": raison.value,
            "message": message,
            "valide": etat.valide,
            "activee": etat.activee,
            "niveau": niveau.value,
            # Premium = licence valide ET niveau payant. Un FREE valide ouvre
            # l'application, avec les limites de la version d'essai.
            "premium": etat.valide and niveau.premium,
            "cle_masquee": masquer_cle(donnees.get("cle", "")),
            "licence_id": rev.get("sub", "") if rev else donnees.get("licence_id", ""),
            "derniere_verification": donnees.get("derniere_validation"),
            "hors_ligne_jusqua": fin_hors_ligne,
            "expire_le": rev.get("exp") if rev else None,
            "horloge_reculee": bool(donnees.get("recul_signale")),
            "horloge_decalee": bool(donnees.get("horloge_decalee")),
            "derniere_erreur": donnees.get("derniere_erreur", ""),
            "revalidation_due": (self._revalidation_due(donnees, rev, locale) if rev
                                 else self._reprise_due(donnees, locale)),
        }

    def _reprise_due(self, donnees: Dict[str, Any], locale: float) -> bool:
        """Faut-il redemander à Novia une licence qu'il a refusée ?

        Seulement pour un refus RATTRAPABLE (expirée, suspendue), et au plus
        toutes les DELAI_NOUVEL_ESSAI_S — une installation abandonnée ne doit
        pas solliciter le serveur à chaque heure.
        """
        if not donnees.get("cle"):
            return False
        if str(donnees.get("statut_serveur") or "") not in ETATS_RATTRAPABLES:
            return False
        return locale - float(donnees.get("derniere_tentative") or 0) >= DELAI_NOUVEL_ESSAI_S

    def _revalidation_due(self, donnees: Dict[str, Any], rev: Dict[str, Any],
                          locale: float) -> bool:
        intervalle = float(lconfig.LICENSE_CHECK_INTERVAL_JOURS) * JOUR
        derniere = float(donnees.get("derniere_validation") or 0)
        tentative = float(donnees.get("derniere_tentative") or 0)
        if locale - tentative < DELAI_NOUVEL_ESSAI_S and tentative > derniere:
            return False                 # un essai vient d'échouer : patienter
        if locale - derniere >= intervalle:
            return True
        return self._fin_hors_ligne(rev) - locale <= MARGE_AVANT_FIN_HORS_LIGNE_S

    # ═══════════════════════ ÉCHANGES AVEC NOVIA ══════════════════════════

    def activer(self, cle_brute: str) -> Dict[str, Any]:
        """Active une clé. Remplace l'activation en cours SEULEMENT si la
        nouvelle réussit : un utilisateur n'est jamais laissé sans licence
        parce qu'il a mal tapé la suivante."""
        cle = normaliser_cle(cle_brute)
        if not cle:
            journal.info("Activation refusée localement : format de clé invalide.")
            return self._echec("Cette clé de licence est invalide.", LicenseState.INVALID,
                               Raison.CLE_INCONNUE)
        if not self.configure():
            return self._echec(MESSAGES_RAISON[Raison.NON_CONFIGURE],
                               LicenseState.NOT_ACTIVATED, Raison.NON_CONFIGURE)

        with self._verrou:
            precedente = self._lire()
            try:
                reponse = self.client.activer(cle, self.installation_id(), _version_app())
            except ErreurNovia as e:
                journal.warning("Activation de %s impossible : %s", masquer_cle(cle), e)
                return self._echec_reseau(e)

            if not reponse.valide:
                etat, raison, message = self._refus(reponse)
                journal.info("Activation refusée pour %s : %s (%s)", masquer_cle(cle),
                             reponse.statut, reponse.code_erreur or "-")
                return self._echec(message, etat, raison)

            try:
                donnees = self._enregistrer_jeton(cle, reponse, precedente)
            except jeton_novia.JetonRefuse as e:
                journal.error("Jeton d'activation refusé pour %s : %s", masquer_cle(cle), e)
                return self._echec(str(e), LicenseState.INVALID, Raison(e.raison))

            ancienne = precedente.get("cle")
            if ancienne and ancienne != cle:
                self._liberer_ancienne(ancienne)
            journal.info("Activation réussie : %s, niveau %s", masquer_cle(cle),
                         donnees.get("niveau"))
            resultat = self.etat()
            resultat.update(success=True, message="Licence activée.")
            return resultat

    def changer(self, cle_brute: str) -> Dict[str, Any]:
        """« Changer de licence » : même garantie qu'`activer`."""
        return self.activer(cle_brute)

    def revalider(self, forcer: bool = False) -> Dict[str, Any]:
        """Revalidation en ligne, si elle est due (ou si `forcer`).

        Ne bloque JAMAIS l'utilisateur sur une panne réseau : l'état retombe
        alors sur la licence locale signée, jusqu'à sa limite hors ligne.
        """
        with self._verrou:
            if not self.configure():
                return self.etat()
            donnees = self._lire()
            cle = donnees.get("cle")
            if not cle:
                return self.etat()
            courant = self.etat()
            if not forcer and not courant.get("revalidation_due"):
                return courant

            donnees["derniere_tentative"] = float(self._horloge())
            try:
                reponse = self.client.valider(cle, self.installation_id(), _version_app())
            except ErreurNovia as e:
                donnees["derniere_erreur"] = e.type_erreur
                self._ecrire(donnees)
                journal.warning("Validation de %s impossible : %s", masquer_cle(cle), e)
                resultat = self.etat()
                resultat.update(success=False, erreur=e.type_erreur,
                                message=self._message_reseau(e, resultat))
                return resultat

            if reponse.valide:
                try:
                    self._enregistrer_jeton(cle, reponse, donnees)
                except jeton_novia.JetonRefuse as e:
                    journal.error("Jeton de validation refusé pour %s : %s",
                                  masquer_cle(cle), e)
                    resultat = self.etat()
                    resultat.update(success=False, message=str(e))
                    return resultat
                journal.info("Validation réussie : %s", masquer_cle(cle))
                resultat = self.etat()
                resultat.update(success=True, message="Licence revérifiée.")
                return resultat

            etat, raison, message = self._refus(reponse)
            # Refus explicite : le jeton local est EFFACÉ. Il pourrait encore
            # être valable hors ligne, et c'est précisément ce qu'il ne faut
            # plus permettre à une licence révoquée ou résiliée.
            donnees.pop("jeton", None)
            donnees["statut_serveur"] = etat.value
            donnees["raison"] = raison.value
            donnees.pop("derniere_erreur", None)
            self._ecrire(donnees)
            self._memo_jeton = None
            journal.warning("Validation : licence %s passée à %s",
                            masquer_cle(cle), etat.value)
            resultat = self.etat()
            resultat.update(success=False, message=message)
            return resultat

    def desactiver(self, forcer: bool = False) -> Dict[str, Any]:
        """Libère cet appareil chez Novia, puis efface l'activation LOCALE.

        Sans confirmation du serveur, rien n'est effacé — sauf `forcer` : un
        effacement local seul laisserait l'emplacement d'appareil occupé chez
        Novia, et l'utilisateur ne pourrait pas activer ailleurs.
        """
        with self._verrou:
            donnees = self._lire()
            cle = donnees.get("cle")
            if not cle:
                return {"success": True, "message": "Aucune licence à désactiver.",
                        **self.etat()}
            try:
                reponse = self.client.desactiver(cle, self.installation_id())
                confirme = reponse.valide or reponse.code_erreur in (
                    "INSTALLATION_NOT_FOUND", "INVALID_KEY", "LICENSE_REVOKED")
            except ErreurNovia as e:
                if not forcer:
                    journal.warning("Désactivation de %s impossible : %s",
                                    masquer_cle(cle), e)
                    resultat = self.etat()
                    resultat.update(
                        success=False, erreur=e.type_erreur,
                        message="Impossible de contacter le serveur de licences : "
                                "cet appareil n'a pas été désactivé. Réessayez "
                                "une fois connecté à Internet.")
                    return resultat
                confirme = False

            if not confirme and not forcer:
                resultat = self.etat()
                resultat.update(success=False,
                                message=reponse.message or
                                "Le serveur de licences a refusé la désactivation.")
                return resultat

            self._effacer()
            journal.info("Appareil désactivé%s : %s",
                         "" if confirme else " localement (sans confirmation serveur)",
                         masquer_cle(cle))
            resultat = self.etat()
            resultat.update(
                success=True,
                message="Cet appareil est désactivé. Vos projets et vos données "
                        "locales sont intacts." + ("" if confirme else
                        " L'emplacement reste occupé chez Novia : libérez-le "
                        "depuis votre espace client."))
            return resultat

    # ── Internes ──
    def _enregistrer_jeton(self, cle: str, reponse, precedente: Dict[str, Any]) -> Dict[str, Any]:
        """Vérifie le jeton reçu PUIS l'enregistre. Rien n'est écrit avant
        que la signature soit prouvée."""
        locale = float(self._horloge())
        base = max(locale, float(reponse.heure_serveur or 0))
        rev = jeton_novia.verifier(reponse.jeton, self.cles_publiques(), self.produit,
                                   self.installation_id(), base, _version_app(),
                                   tolerance_s=JOUR)
        iat = float(rev["iat"])
        donnees = dict(precedente) if precedente.get("cle") == cle else {}
        donnees.update(cle=cle, jeton=reponse.jeton, statut_serveur="ACTIVE",
                       niveau=LicenseTier.depuis(rev.get("tier")).value,
                       licence_id=str(rev.get("sub") or ""),
                       derniere_validation=max(locale, iat),
                       derniere_tentative=locale)
        donnees.setdefault("active_le", locale)
        for cle_obsolete in ("raison", "derniere_erreur", "recul_signale"):
            donnees.pop(cle_obsolete, None)

        # La SEULE façon de faire redescendre l'heure vue : une validation en
        # ligne réussie, avec une horloge locale d'accord avec l'heure signée.
        if abs(locale - iat) <= DECALAGE_SIGNALE_S:
            donnees["heure_vue"] = locale
            donnees.pop("horloge_decalee", None)
        else:
            donnees["heure_vue"] = max(float(donnees.get("heure_vue") or 0), iat)
            donnees["horloge_decalee"] = True
            journal.warning("Horloge locale décalée de %.1f jour(s) par rapport au "
                            "serveur de licences.", (locale - iat) / JOUR)
        self._ecrire(donnees)
        self._memo_jeton = (reponse.jeton, rev)
        return donnees

    def _liberer_ancienne(self, ancienne: str) -> None:
        """Changement de licence : libère l'ancienne chez Novia, sans bloquer."""
        try:
            self.client.desactiver(ancienne, self.installation_id())
            journal.info("Ancienne licence libérée : %s", masquer_cle(ancienne))
        except ErreurNovia as e:
            journal.warning("Ancienne licence %s non libérée : %s",
                            masquer_cle(ancienne), e)

    def _refus(self, reponse):
        """(état, raison, message) d'une réponse négative du serveur."""
        code = reponse.code_erreur
        if code == "DEVICE_LIMIT_REACHED":
            return (LicenseState.INVALID, Raison.APPAREILS,
                    "Le nombre maximal d'appareils autorisés est atteint.")
        if code == "INVALID_KEY" or reponse.statut == "INVALID":
            return (LicenseState.INVALID, Raison.CLE_INCONNUE,
                    "Cette clé de licence est invalide.")
        if code == "LICENSE_EXPIRED" or reponse.statut == "EXPIRED":
            return LicenseState.EXPIRED, Raison.LICENCE_ECHUE, "Votre licence a expiré."
        if code == "LICENSE_SUSPENDED" or reponse.statut == "SUSPENDED":
            return (LicenseState.SUSPENDED, Raison.AUCUNE,
                    MESSAGES[LicenseState.SUSPENDED])
        if code == "LICENSE_REVOKED" or reponse.statut == "REVOKED":
            return (LicenseState.REVOKED, Raison.AUCUNE, MESSAGES[LicenseState.REVOKED])
        if code == "PRODUCT_MISMATCH":
            return (LicenseState.INVALID, Raison.AUTRE_PRODUIT,
                    MESSAGES_RAISON[Raison.AUTRE_PRODUIT])
        return (LicenseState.INVALID, Raison.CLE_INCONNUE,
                reponse.message or "Cette clé de licence est invalide.")

    def _echec(self, message: str, etat: LicenseState, raison: Raison) -> Dict[str, Any]:
        resultat = self.etat()
        resultat.update(success=False, message=message, refus_etat=etat.value,
                        refus_raison=raison.value)
        return resultat

    def _echec_reseau(self, e: ErreurNovia) -> Dict[str, Any]:
        resultat = self.etat()
        resultat.update(success=False, erreur=e.type_erreur,
                        message=self._message_reseau(e, resultat))
        return resultat

    @staticmethod
    def _message_reseau(e: ErreurNovia, etat: Dict[str, Any]) -> str:
        if isinstance(e, (ServeurIndisponible,)):
            base = str(e)
        elif isinstance(e, CertificatRefuse):
            base = str(e)
        elif isinstance(e, (ConfigurationInvalide, ReponseInvalide)):
            base = f"Le serveur de licences a renvoyé une réponse inexploitable : {e}"
        else:
            base = "Impossible de contacter le serveur de licences."
        if etat.get("valide") and etat.get("hors_ligne_jusqua"):
            base += (" Votre licence locale reste valide jusqu'au "
                     f"{_date(etat['hors_ligne_jusqua'])}.")
        return base


# ═══════════════════════════ INSTANCE PARTAGÉE ════════════════════════════

_instance: Optional[LicenseManager] = None
_instance_verrou = threading.Lock()


def manager() -> LicenseManager:
    """Le gestionnaire de l'application, construit depuis la configuration."""
    global _instance
    if _instance is None:
        with _instance_verrou:
            if _instance is None:
                _instance = LicenseManager()
    return _instance


def reinitialiser(nouveau: Optional[LicenseManager] = None) -> None:
    """Remplace l'instance (tests, ou configuration modifiée à chaud)."""
    global _instance
    with _instance_verrou:
        _instance = nouveau
