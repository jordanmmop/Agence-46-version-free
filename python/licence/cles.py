"""Clés d'abonnement — ce que l'abonné reçoit quand son paiement est confirmé.

À QUOI SERT UNE CLÉ
-------------------
C'est la PREUVE d'abonnement remise à l'utilisateur : une courte chaîne qu'il
reçoit par e-mail et par SMS dès que Stripe confirme le règlement, qu'il garde,
et qu'il colle dans « Passer à la version Pro » pour débloquer l'application —
y compris sur un poste où il n'est pas connecté à son compte.

    AGF-7K3QM-9XZ2P-R4TB8

FORMAT
------
Trois groupes de cinq caractères tirés d'un alphabet de 32 sans caractères
ambigus (ni I, ni L, ni O, ni U) : 75 bits d'aléa. C'est ce qui permet de la
dicter au téléphone ou de la recopier depuis un SMS sans se tromper entre
« 0 » et « O ». À la lecture, « O » est relu « 0 », « I » et « L » relus « 1 »,
« U » relu « V » : l'utilisateur qui tape ce qu'il croit voir tombe juste.

CE QUI EST STOCKÉ EN BASE
-------------------------
L'EMPREINTE de la clé (SHA-256), jamais la clé elle-même. Une clé est un
porteur de droits, au même titre qu'un mot de passe : une fuite de la base ne
doit pas livrer des abonnements utilisables. La conséquence est assumée — même
l'éditeur ne peut pas réafficher une clé perdue, il ne peut qu'en émettre une
nouvelle (voir `remplacer`).

Pas de sel : contrairement à un mot de passe choisi par un humain, une clé est
tirée au hasard sur 75 bits. Il n'existe pas de dictionnaire à précalculer, et
un sel par clé n'apporterait rien ici. Même raisonnement que pour les jetons
de session, qui sont stockés de la même façon.

Les CINQ DERNIERS caractères sont conservés en clair (`indice`). Ils ne
permettent pas de reconstituer la clé — il reste 50 bits — et ils servent à
l'utilisateur qui en détient plusieurs à savoir laquelle est laquelle.

CE QU'UNE CLÉ NE FAIT PAS
-------------------------
Elle n'ouvre aucun droit par elle-même : à la validation, c'est l'état RÉEL du
compte en base qui décide. Une clé émise pour un abonnement annuel devient
inopérante dès que le compte n'est plus abonné, sans qu'il faille la révoquer.
L'inverse est vrai aussi : un renouvellement prolonge le compte, donc la clé
déjà remise reste valable — l'abonné n'a pas à en recevoir une nouvelle chaque
mois.
"""
import hashlib
import logging
import re
import secrets
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PREFIXE = "AGF"
# Alphabet de Crockford : les 32 caractères sans I, L, O ni U.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
GROUPES = 3
TAILLE_GROUPE = 5
LONGUEUR = GROUPES * TAILLE_GROUPE

# Confusions corrigées à la lecture. « U » est écarté de l'alphabet parce qu'il
# se confond avec « V » à l'oral comme à l'écrit sur certaines polices.
_CORRECTIONS = {"O": "0", "I": "1", "L": "1", "U": "V"}


def generer() -> str:
    """Une nouvelle clé, tirée au hasard. 75 bits — jamais dérivée du compte.

    `secrets` et non `random` : le second est prévisible à partir de quelques
    tirages, et une clé devinable vaut un abonnement offert.
    """
    brut = "".join(secrets.choice(ALPHABET) for _ in range(LONGUEUR))
    groupes = [brut[i:i + TAILLE_GROUPE] for i in range(0, LONGUEUR, TAILLE_GROUPE)]
    return f"{PREFIXE}-" + "-".join(groupes)


def normaliser(brut: str) -> str:
    """Forme canonique d'une clé saisie : majuscules, confusions corrigées.

    Accepte tout ce qu'un utilisateur peut coller : espaces, tirets absents ou
    en trop, minuscules, préfixe oublié, « AGF » recopié deux fois. Renvoie une
    chaîne vide si ce n'est pas une clé de ce format — et surtout jamais une
    exception : la saisie vient d'un formulaire.
    """
    texte = (brut or "").strip().upper()
    texte = re.sub(r"[^0-9A-Z]", "", texte)
    while texte.startswith(PREFIXE):
        texte = texte[len(PREFIXE):]
    corps = "".join(_CORRECTIONS.get(c, c) for c in texte)
    if len(corps) != LONGUEUR or any(c not in ALPHABET for c in corps):
        return ""
    groupes = [corps[i:i + TAILLE_GROUPE] for i in range(0, LONGUEUR, TAILLE_GROUPE)]
    return f"{PREFIXE}-" + "-".join(groupes)


def empreinte(cle: str) -> str:
    """SHA-256 de la clé NORMALISÉE, en hexadécimal. Chaîne vide si invalide.

    Normaliser AVANT de hacher est essentiel : sans cela « agf7k3qm… » et
    « AGF-7K3QM-… » donneraient deux empreintes différentes, et la clé
    recopiée depuis un SMS ne retrouverait pas sa ligne en base.
    """
    canonique = normaliser(cle)
    if not canonique:
        return ""
    return hashlib.sha256(canonique.encode("ascii")).hexdigest()


def indice(cle: str) -> str:
    """Les cinq derniers caractères — de quoi reconnaître une clé, pas la deviner."""
    canonique = normaliser(cle)
    return canonique[-TAILLE_GROUPE:] if canonique else ""


def masquer(indice_clair: str) -> str:
    """Forme affichable d'une clé dont on n'a plus que l'indice."""
    voiles = "-".join("•" * TAILLE_GROUPE for _ in range(GROUPES - 1))
    return f"{PREFIXE}-{voiles}-{indice_clair}" if indice_clair else ""


# ═══════════════════════════ ÉMISSION ═════════════════════════════════════

def emettre(compte_id: str, formule: str = "", reference: str = "",
            expire_le: Optional[float] = None) -> Dict[str, Any]:
    """Émet une clé pour un compte, UNE SEULE FOIS par référence de paiement.

    Le même règlement est annoncé deux fois : par le webhook Stripe, puis par
    le retour du navigateur sur `/api/abonnement/retour`. Sans cette garde,
    l'abonné recevrait deux clés et deux SMS pour un seul paiement — et la
    seconde émission écraserait la première dans son esprit.

    Renvoie `{"cle": ..., "deja_emise": bool}`. `cle` est VIDE quand une clé
    existe déjà pour cette référence : elle n'est pas relisible, seulement
    remplaçable (`remplacer`).
    """
    from licence import comptes

    reference = str(reference or "")[:200]
    if reference:
        existante = comptes.depot().cle_par_reference(reference)
        if existante and not existante.get("revoquee_le"):
            logger.info("[cles] Clé déjà émise pour le paiement %s — pas de doublon",
                        reference)
            return {"cle": "", "deja_emise": True, "enregistrement": existante}

    cle = generer()
    enregistrement = {
        "cle_hash": empreinte(cle),
        "compte_id": str(compte_id),
        "formule": str(formule or ""),
        "reference": reference,
        "indice": indice(cle),
        "cree_le": time.time(),
        "expire_le": float(expire_le) if expire_le else None,
        "revoquee_le": None,
        "derniere_utilisation": None,
        "utilisations": 0,
        "envoi": "",
    }
    comptes.depot().creer_cle(enregistrement)
    logger.info("[cles] Clé %s émise pour le compte %s (formule %s)",
                masquer(enregistrement["indice"]), compte_id, formule or "?")
    return {"cle": cle, "deja_emise": False, "enregistrement": enregistrement}


def remplacer(compte_id: str, formule: str = "",
              expire_le: Optional[float] = None) -> Dict[str, Any]:
    """Révoque les clés du compte et en émet une nouvelle.

    C'est le chemin de secours : clé perdue, e-mail jamais reçu, téléphone
    changé. Révoquer les anciennes est volontaire — une clé qui circule dans
    une boîte mail oubliée reste un droit d'accès, et l'abonné qui en demande
    une nouvelle le fait généralement parce qu'il a perdu la trace de la
    précédente.

    Les installations DÉJÀ activées ne sont pas affectées : elles détiennent
    un jeton signé, obtenu au moment de l'activation, qui vit sa propre vie
    jusqu'à son échéance.
    """
    revoquer(compte_id)
    # Pas de référence de paiement : c'est une réémission, pas un encaissement.
    return emettre(compte_id, formule, "", expire_le)


def revoquer(compte_id: str) -> int:
    """Révoque toutes les clés en cours d'un compte. Renvoie le nombre touché."""
    from licence import comptes
    nombre = comptes.depot().revoquer_cles(compte_id, time.time())
    if nombre:
        logger.info("[cles] %d clé(s) révoquée(s) pour le compte %s", nombre, compte_id)
    return nombre


def marquer_envoi(cle_hash: str, canaux: str) -> None:
    """Consigne par quels canaux la clé est effectivement partie."""
    from licence import comptes
    if cle_hash:
        comptes.depot().modifier_cle(cle_hash, envoi=str(canaux or "")[:50])


# ═══════════════════════════ VALIDATION ═══════════════════════════════════

def valider(cle: str) -> Dict[str, Any]:
    """La clé présentée ouvre-t-elle des droits Pro, et pour quel compte ?

    Ne lève jamais et ne dit jamais oui par défaut : toute anomalie — format,
    clé inconnue, révoquée, compte disparu, abonnement terminé — vaut refus.

    L'échéance retenue est celle du COMPTE, pas celle figée dans la clé : un
    renouvellement prolonge le compte, et la clé déjà remise doit suivre sans
    qu'il faille en réémettre une à chaque prélèvement mensuel.
    """
    from licence import comptes
    from licence.etat import EtatLicence

    canonique = normaliser(cle)
    if not canonique:
        return {"valide": False, "erreur": "Cette clé d'abonnement n'a pas le bon "
                                           "format (AGF-XXXXX-XXXXX-XXXXX)."}

    enregistrement = comptes.depot().cle_par_hash(empreinte(canonique))
    if not enregistrement:
        return {"valide": False, "erreur": "Clé d'abonnement inconnue."}
    if enregistrement.get("revoquee_le"):
        return {"valide": False,
                "erreur": "Cette clé a été remplacée. Utilisez la dernière reçue."}

    compte = comptes.depot().par_id(str(enregistrement.get("compte_id") or ""))
    if not compte:
        return {"valide": False, "erreur": "Le compte rattaché à cette clé n'existe plus."}

    etat = comptes.etat_du_compte(compte)
    if not etat.est_pro:
        return {"valide": False, "etat": etat.value,
                "erreur": "L'abonnement rattaché à cette clé n'est plus actif."}

    echeance = compte.get("abonne_jusqua")
    comptes.depot().modifier_cle(
        enregistrement["cle_hash"],
        derniere_utilisation=time.time(),
        utilisations=int(enregistrement.get("utilisations") or 0) + 1)

    return {
        "valide": True,
        "compte_id": compte["id"],
        "email": compte.get("email", ""),
        "formule": compte.get("formule") or enregistrement.get("formule") or "",
        "expire_le": float(echeance) if echeance else None,
        "etat": EtatLicence.PRO_ACTIVE.value,
    }


def cles_du_compte(compte_id: str) -> List[Dict[str, Any]]:
    """Clés d'un compte, vue destinée à l'affichage — jamais la clé en clair."""
    from licence import comptes
    lignes = comptes.depot().cles_du_compte(str(compte_id or ""))
    return [{
        "apercu": masquer(str(l.get("indice") or "")),
        "formule": l.get("formule") or "",
        "cree_le": l.get("cree_le"),
        "expire_le": l.get("expire_le"),
        "revoquee": bool(l.get("revoquee_le")),
        "envoi": l.get("envoi") or "",
        "utilisations": int(l.get("utilisations") or 0),
    } for l in lignes]
