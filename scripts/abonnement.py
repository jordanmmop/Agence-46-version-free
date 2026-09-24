#!/usr/bin/env python3
"""Outil d'administration des abonnements — à lancer SUR LE SERVEUR.

    python scripts/abonnement.py etat    alice@exemple.fr
    python scripts/abonnement.py cle     alice@exemple.fr
    python scripts/abonnement.py activer alice@exemple.fr --formule annuel \\
                                         --reference pi_3Q... [--jours 372]
    python scripts/abonnement.py canaux

À QUOI ÇA SERT
--------------
À rattraper un cas que l'automatisme n'a pas pu traiter : un règlement
encaissé alors que `STRIPE_WEBHOOK_SECRET` n'était pas encore configuré, un
webhook perdu, un abonné dont l'e-mail n'est jamais arrivé. Sans cet outil, la
seule issue serait un remboursement et un nouveau paiement.

CE QUE CET OUTIL NE FAIT PAS
----------------------------
Il ne constate aucun paiement et n'en simule aucun. `activer` ouvre des droits
parce que VOUS affirmez avoir vu le règlement dans votre tableau de bord
Stripe — la référence demandée sert à retrouver cette preuve plus tard. C'est
une décision d'administrateur, tracée comme telle, pas une vérification
automatique : celle-ci reste le webhook signé et la relecture de session.

Il ne peut pas non plus réafficher une clé déjà remise : la base n'en conserve
que l'empreinte. `cle` en émet une NOUVELLE et révoque la précédente.
"""
import argparse
import os
import sys
import time
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RACINE / "python"))
sys.path.insert(0, str(_RACINE))


def _compte(identifiant: str):
    from licence import comptes
    trouve = (comptes.depot().par_email(identifiant)
              or comptes.depot().par_telephone(identifiant)
              or comptes.depot().par_id(identifiant))
    if not trouve:
        print(f"✗ Aucun compte pour « {identifiant} ».")
        raise SystemExit(2)
    return trouve


def _date(horodatage) -> str:
    if not horodatage:
        return "—"
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(float(horodatage)))


def cmd_etat(args) -> int:
    from licence import cles, comptes
    compte = _compte(args.compte)
    etat = comptes.etat_du_compte(compte)
    print(f"Compte      : {compte['email']}  ({compte['id']})")
    print(f"Téléphone   : {compte['telephone']}")
    print(f"État        : {etat.value} — {etat.libelle}")
    print(f"Formule     : {compte.get('formule') or '—'}")
    print(f"Essai jusqu': {_date(compte.get('essai_fin'))}")
    print(f"Abonné jusq': {_date(compte.get('abonne_jusqua'))}")
    print(f"Réf. paiem. : {compte.get('paiement_ref') or '—'}")
    listes = cles.cles_du_compte(compte["id"])
    print(f"Clés émises : {len(listes)}")
    for ligne in listes:
        marque = "révoquée" if ligne["revoquee"] else "active"
        envoi = ligne["envoi"] or "non remise"
        print(f"  {ligne['apercu']}  {_date(ligne['cree_le'])}  "
              f"{marque}, {envoi}, {ligne['utilisations']} activation(s)")
    return 0


def cmd_activer(args) -> int:
    from licence import comptes, stripe_paiement
    compte = _compte(args.compte)
    if not args.reference:
        print("✗ --reference est obligatoire : indiquez l'identifiant du\n"
              "  paiement tel qu'il apparaît dans votre tableau de bord Stripe.\n"
              "  C'est la seule trace qui rattachera ces droits à un règlement.")
        return 2

    compte = comptes.activer_abonnement(compte["id"], args.formule,
                                        args.reference, args.jours)
    print(f"✔ Droits {args.formule} ouverts jusqu'au "
          f"{_date(compte.get('abonne_jusqua'))}")

    resultat = stripe_paiement.remettre_cle(compte, args.formule, args.reference)
    if resultat.get("cle"):
        print(f"\n  CLÉ D'ABONNEMENT : {resultat['cle']}")
        print(f"  {resultat.get('cle_message', '')}")
        print("\n  Cette clé ne sera PAS réaffichée : transmettez-la maintenant\n"
              "  si l'envoi automatique n'a pas abouti.")
    elif resultat.get("cle_deja_emise"):
        print("\n  Une clé avait déjà été émise pour cette référence de paiement.")
        print("  Pour en remettre une nouvelle : "
              f"python scripts/abonnement.py cle {compte['email']}")
    return 0


def cmd_cle(args) -> int:
    from licence import cles, comptes, notifications
    compte = _compte(args.compte)
    etat = comptes.etat_du_compte(compte)
    if not etat.est_pro:
        print(f"✗ Ce compte n'est pas abonné (état {etat.value}) : il n'y a pas\n"
              f"  de clé à remettre. Utilisez « activer » si le paiement est\n"
              f"  confirmé de votre côté.")
        return 2

    emission = cles.remplacer(compte["id"], compte.get("formule") or "",
                              compte.get("abonne_jusqua"))
    cle = emission.get("cle", "")
    if not cle:
        print("✗ La clé n'a pas pu être émise.")
        return 1
    envoi = notifications.envoyer_cle(compte, cle, compte.get("formule") or "")
    cles.marquer_envoi(emission["enregistrement"]["cle_hash"], envoi.get("canaux", ""))

    print(f"\n  CLÉ D'ABONNEMENT : {cle}\n")
    for detail in envoi["details"]:
        print(f"  · {detail}")
    print("\n  La clé précédente est révoquée. Celle-ci ne sera pas réaffichée.")
    return 0


def cmd_canaux(args) -> int:
    from licence import notifications
    diag = notifications.diagnostic()
    print("Remise des clés d'abonnement")
    print(f"  E-mail : {'configuré' if diag['email'] else 'NON configuré'}"
          f"  (SMTP_HOTE={diag['email_hote'] or '—'},"
          f" expéditeur {diag['email_expediteur'] or '—'})")
    print(f"  SMS    : {'configuré' if diag['sms'] else 'NON configuré'}"
          f"  (fournisseur {diag['sms_fournisseur'] or '—'})")
    if not (diag["email"] or diag["sms"]):
        print("\n  Aucun canal : la clé restera affichée sur la page de retour\n"
              "  et dans l'application, mais ne sera envoyée nulle part.\n"
              "  Voir python/.env.example, section « Remise des clés ».")
    return 0


def main(argv=None) -> int:
    analyseur = argparse.ArgumentParser(
        description="Administration des abonnements (à lancer sur le serveur).")
    sous = analyseur.add_subparsers(dest="commande", required=True)

    p = sous.add_parser("etat", help="État d'un compte et de ses clés")
    p.add_argument("compte", help="e-mail, téléphone ou identifiant")
    p.set_defaults(fonction=cmd_etat)

    p = sous.add_parser("activer", help="Ouvrir des droits après un paiement constaté")
    p.add_argument("compte")
    p.add_argument("--formule", default="mensuel", choices=("mensuel", "annuel"))
    p.add_argument("--reference", default="",
                   help="Identifiant Stripe du règlement (obligatoire)")
    p.add_argument("--jours", type=float, default=None,
                   help="Durée des droits ; par défaut celle de la formule")
    p.set_defaults(fonction=cmd_activer)

    p = sous.add_parser("cle", help="Émettre et envoyer une NOUVELLE clé")
    p.add_argument("compte")
    p.set_defaults(fonction=cmd_cle)

    p = sous.add_parser("canaux", help="Ce qui est configuré pour l'envoi")
    p.set_defaults(fonction=cmd_canaux)

    args = analyseur.parse_args(argv)
    # `.env` du serveur : sans lui, l'outil ne verrait ni la base centrale ni
    # les identifiants d'envoi, et travaillerait sur la base locale.
    _charger_env()
    return args.fonction(args)


def _charger_env() -> None:
    fichier = _RACINE / "python" / ".env"
    if not fichier.exists():
        return
    try:
        for ligne in fichier.read_text().splitlines():
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#") or "=" not in ligne:
                continue
            nom, _, valeur = ligne.partition("=")
            # L'environnement réel l'emporte : un opérateur qui exporte une
            # variable avant de lancer l'outil veut qu'elle serve.
            os.environ.setdefault(nom.strip(), valeur.strip().strip('"').strip("'"))
    except Exception as e:
        print(f"⚠ python/.env illisible : {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
