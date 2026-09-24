#!/usr/bin/env python3
"""Outil d'administration des abonnements — à lancer SUR LE SERVEUR.

    python scripts/abonnement.py etat    alice@exemple.fr
    python scripts/abonnement.py cle     alice@exemple.fr
    python scripts/abonnement.py activer alice@exemple.fr --formule annuel \\
                                         --reference pi_3Q... [--jours 372]
    python scripts/abonnement.py canaux
    python scripts/abonnement.py diagnostic

SANS SERVEUR — émission manuelle des licences :

    python scripts/abonnement.py emetteur                      (une seule fois)
    python scripts/abonnement.py licence alice@exemple.fr --formule annuel

Ces deux commandes n'ont besoin NI de serveur, NI de base partagée : le jeton
produit se vérifie par SIGNATURE sur le poste du client, hors ligne. C'est le
seul chemin praticable tant qu'aucune machine joignable par Stripe n'héberge
l'application — voir DEPLOIEMENT.md.

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


def cmd_emetteur(args) -> int:
    """Crée (au premier appel) et affiche la paire de clés de l'émetteur.

    La clé PUBLIQUE doit être posée dans l'application AVANT compilation : sans
    elle, les postes de vos clients n'ont aucun moyen de reconnaître vos
    licences, et toute activation échoue. La clé PRIVÉE ne quitte jamais cette
    machine — c'est elle qui fait de vous l'émetteur.
    """
    from licence import emetteur

    privee = emetteur.cle_privee(creer=True)
    if not privee:
        print("✗ Impossible de créer la paire de clés : le module "
              "« cryptography » est absent.\n  pip install cryptography")
        return 1
    publique = emetteur.cle_publique_hex()
    if not publique:
        print("✗ Clé publique illisible.")
        return 1

    chemin = emetteur._chemin_fichier()
    print("Émetteur de licences de cette machine\n")
    print(f"  Clé privée : {chemin}")
    print("               NE LA PARTAGEZ JAMAIS, ne la sauvegardez pas dans")
    print("               un dossier synchronisé public. Qui la détient peut")
    print("               fabriquer des abonnements Pro.")
    print("               Gardez-en une copie hors ligne : la perdre invalide")
    print("               toutes les licences déjà remises à vos clients.\n")
    print(f"  Clé PUBLIQUE (ce n'est pas un secret) :\n\n    {publique}\n")
    print("  À coller dans python/licence/verification.py AVANT de compiler :\n")
    print(f'    CLE_PUBLIQUE_EMETTEUR = "{publique}"\n')
    print("  Puis recompilez et rediffusez l'application : les versions déjà")
    print("  installées chez vos clients n'accepteront pas vos licences tant")
    print("  qu'elles n'embarquent pas cette clé.")
    return 0


def cmd_licence(args) -> int:
    """Signe une licence Pro pour un client, et la lui envoie.

    NE CONSTATE AUCUN PAIEMENT. Vous lancez cette commande parce que vous avez
    vu le règlement dans votre tableau de bord Stripe ; la référence demandée
    est ce qui vous permettra de le retrouver.

    Aucune base partagée n'est nécessaire : le jeton produit porte sa propre
    preuve — une signature que le poste du client vérifie seul, hors ligne.
    """
    import time as _t
    from licence import emetteur, notifications
    from licence import config as lconfig

    destinataire = (args.compte or "").strip()
    if "@" not in destinataire:
        print("✗ Indiquez l'adresse e-mail du client.")
        return 2
    if not args.reference:
        print("✗ --reference est obligatoire : indiquez l'identifiant du\n"
              "  paiement tel qu'il apparaît dans votre tableau de bord Stripe.\n"
              "  C'est la seule trace qui rattachera cette licence à un règlement.")
        return 2

    jours = args.jours if args.jours else lconfig.DUREE_DROITS_JOURS.get(args.formule, 34)
    echeance = _t.time() + float(jours) * 86400
    jeton = emetteur.emettre(destinataire, echeance)
    if not jeton:
        print("✗ Aucune licence signée : émetteur indisponible.\n"
              "  Lancez d'abord :  python scripts/abonnement.py emetteur")
        return 1

    fin = _t.strftime("%d/%m/%Y", _t.localtime(echeance))
    print(f"\n  Licence {args.formule} pour {destinataire}, valable jusqu'au {fin}")
    print(f"  Référence du règlement : {args.reference}\n")
    print(f"  LICENCE À REMETTRE AU CLIENT :\n\n{jeton}\n")

    # L'envoi réutilise exactement le même chemin que la remise automatique :
    # un seul code d'envoi, donc un seul comportement à vérifier.
    faux_compte = {"id": destinataire, "email": destinataire,
                   "telephone": args.telephone or "", "abonne_jusqua": echeance}
    envoi = notifications.envoyer_cle(faux_compte, jeton, args.formule)
    for detail in envoi["details"]:
        print(f"  · {detail}")
    if not envoi["cle_remise"]:
        print("\n  Aucun envoi n'a abouti : transmettez la licence ci-dessus\n"
              "  vous-même. Elle tient sur une seule ligne, sans espace.")

    print("\n  ⚠️ Une licence signée n'est PAS révocable : elle reste valable\n"
          f"     jusqu'au {fin} même si le client résilie entre-temps.\n"
          "     Pour un abonnement mensuel, émettez des licences mensuelles.")
    return 0


def cmd_diagnostic(args) -> int:
    """« Pourquoi mon client n'a pas reçu sa clé ? » — la chaîne, maillon par maillon.

    Les maillons sont contrôlés DANS L'ORDRE où ils cassent la chaîne : le
    premier qui manque explique tout ce qui suit, et l'outil le dit au lieu
    d'aligner six avertissements qui n'ont qu'une seule cause.
    """
    from licence import comptes, notifications, stripe_paiement
    from licence import config as lconfig

    bloquant = []
    print("Chaîne de remise des clés d'abonnement\n")

    # 1. La base : sans elle, ni compte, ni clé.
    try:
        nombre = comptes.depot().nombre()
        print(f"  1. Base des comptes ........ OK ({nombre} compte(s))")
    except Exception as e:
        print(f"  1. Base des comptes ........ INJOIGNABLE — {e}")
        print("\n  Rien d'autre ne peut fonctionner tant que la base ne répond pas.")
        return 1

    # 2 et 3. Les deux secrets Stripe. Aucun des deux n'est facultatif pour la
    # même raison : sans eux, AUCUN paiement ne peut être CONFIRMÉ, et un
    # paiement non confirmé n'émet pas de clé.
    cle = bool(stripe_paiement.cle_secrete())
    webhook = bool(stripe_paiement.secret_webhook())
    print(f"  2. STRIPE_SECRET_KEY ....... {'OK' if cle else 'ABSENTE'}")
    print(f"  3. STRIPE_WEBHOOK_SECRET ... {'OK' if webhook else 'ABSENT'}")
    if not (cle or webhook):
        bloquant.append(
            "Aucun secret Stripe : ce serveur ne peut confirmer aucun paiement,\n"
            "     donc il n'émet aucune clé. C'est la cause la plus fréquente.\n"
            "     → bash scripts/configurer-serveur.sh")
    elif not webhook:
        bloquant.append(
            "Sans STRIPE_WEBHOOK_SECRET, un client qui ferme son navigateur\n"
            "     après avoir payé n'est jamais confirmé, et les renouvellements\n"
            "     mensuels ne le sont pas non plus.")
    elif not cle:
        bloquant.append(
            "Sans STRIPE_SECRET_KEY, le retour du navigateur ne peut pas être\n"
            "     relu : seul le webhook confirme, et rien ne rattrape s'il tarde.")

    incoherence = stripe_paiement.incoherence_environnement()
    if incoherence:
        bloquant.append(incoherence)

    # 4. Les canaux d'envoi. NON bloquants : sans eux la clé existe quand même,
    # elle s'affiche à l'écran. Le dire évite de chercher au mauvais endroit.
    diag = notifications.diagnostic()
    print(f"  4. Envoi e-mail ............ {'OK' if diag['email'] else 'non configuré'}")
    print(f"  5. Envoi SMS ............... "
          f"{diag['sms_fournisseur'] if diag['sms'] else 'non configuré'}")

    # 6. Ce que la base raconte : c'est ici qu'on voit si des paiements
    # arrivent réellement.
    abonnes = paiements = cles_emises = cles_remises = 0
    for ligne in _tous_les_comptes():
        etat = comptes.etat_du_compte(ligne)
        if etat.est_pro:
            abonnes += 1
        if ligne.get("paiement_ref"):
            paiements += 1
        for k in comptes.depot().cles_du_compte(ligne["id"]):
            cles_emises += 1
            if k.get("envoi"):
                cles_remises += 1
    print(f"\n  Comptes abonnés ............ {abonnes}")
    print(f"  Paiements enregistrés ...... {paiements}")
    print(f"  Clés émises ................ {cles_emises}")
    print(f"  Clés effectivement remises . {cles_remises}")

    if paiements == 0 and (cle or webhook):
        bloquant.append(
            "Les secrets sont en place mais AUCUN paiement n'est jamais arrivé\n"
            "     jusqu'à ce serveur. Vérifiez dans Stripe → Développeurs →\n"
            "     Webhooks que l'endpoint existe et que ses tentatives\n"
            "     aboutissent :  https://<votre-domaine>/api/abonnement/webhook\n"
            "     Stripe n'émet pas ses webhooks en IPv6, et ne peut pas\n"
            "     joindre une machine sans adresse publique.")
    elif cles_emises and not cles_remises and not (diag["email"] or diag["sms"]):
        bloquant.append(
            "Des clés SONT émises, mais aucun canal d'envoi n'est configuré :\n"
            "     elles s'affichent à l'écran sans partir.\n"
            "     → bash scripts/configurer-serveur.sh --envoi")

    print()
    if not bloquant:
        print("  ✔ Rien ne bloque : un paiement confirmé émet une clé et la remet.")
        return 0
    print("  Ce qui bloque :\n")
    for i, message in enumerate(bloquant, 1):
        print(f"  {i}. {message}\n")
    return 1


def _tous_les_comptes() -> list:
    """Tous les comptes, quel que soit le dépôt.

    Le contrat du dépôt n'expose pas de « lister tout » — il n'en a pas besoin
    ailleurs, et l'ajouter obligerait les deux moteurs à le porter. Ce
    diagnostic est le seul appelant : il lit donc directement, en acceptant de
    connaître le moteur.
    """
    from licence import comptes
    depot = comptes.depot()
    if hasattr(depot, "_conn"):                       # SQLite
        with depot._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM comptes").fetchall()]
    if hasattr(depot, "_executer"):                   # PostgreSQL
        return depot._executer("SELECT * FROM comptes", fetch="tous") or []
    return []


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

    p = sous.add_parser("diagnostic",
                        help="Pourquoi un client n'a pas reçu sa clé")
    p.set_defaults(fonction=cmd_diagnostic)

    p = sous.add_parser("emetteur",
                        help="Créer/afficher la paire de clés de l'émetteur")
    p.set_defaults(fonction=cmd_emetteur)

    p = sous.add_parser("licence",
                        help="Signer une licence pour un client (sans serveur)")
    p.add_argument("compte", help="adresse e-mail du client")
    p.add_argument("--formule", default="mensuel", choices=("mensuel", "annuel"))
    p.add_argument("--reference", default="",
                   help="Identifiant Stripe du règlement (obligatoire)")
    p.add_argument("--telephone", default="",
                   help="Pour envoyer aussi par SMS")
    p.add_argument("--jours", type=float, default=None,
                   help="Durée ; par défaut celle de la formule")
    p.set_defaults(fonction=cmd_licence)

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
