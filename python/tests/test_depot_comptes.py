"""Contrat du dépôt de comptes — vérifié sur CHAQUE moteur de stockage.

Les comptes vivent soit dans le fichier SQLite de la machine (défaut), soit
dans une base PostgreSQL centrale (`AGENCE_COMPTES_DSN`). Les deux doivent se
comporter à l'identique : même unicité, mêmes sessions, mêmes erreurs. Un
écart entre les deux ne se verrait qu'en production, sur la base des clients.

Ce module exécute donc LE MÊME jeu de vérifications contre chaque
implémentation disponible. PostgreSQL est testé si une base de test est
joignable (variable `AGENCE_TEST_PG_DSN`), et ignoré sinon — l'intégration
continue n'a pas de serveur PostgreSQL.
"""
import _setup  # noqa: F401
import os
import secrets
import tempfile
import time
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]


def _compte(n: int) -> dict:
    """Un compte complet, au contact unique."""
    from licence import comptes
    h = secrets.token_hex(3)
    maintenant = time.time()
    email = f"depot{n}.{h}@exemple-tests.invalid"
    tel = f"06{n:03d}{int(h, 16) % 10000:04d}"
    return {
        "id": secrets.token_hex(16),
        "email": email,
        "telephone": tel,
        "email_cle": comptes.cle_email(email),
        "telephone_cle": comptes.cle_telephone(tel),
        "mot_de_passe": comptes.hacher_mot_de_passe("mot-de-passe-test"),
        "nom": "", "adresse": "1 rue", "code_postal": "75011",
        "ville": "Paris", "pays": "France",
        "cree_le": maintenant,
        "essai_fin": maintenant + 3 * 86400,
        "etat": "TRIAL", "formule": "", "abonne_jusqua": None,
        "paiement_ref": "", "derniere_connexion": maintenant,
    }


def _verifier_contrat(depot, nom: str) -> None:
    """Le contrat que TOUT dépôt doit respecter, quel que soit son moteur."""
    from licence.comptes import ContactDejaUtilise, cle_email, cle_telephone

    # ── Création et relectures ──
    c = _compte(1)
    depot.creer(c)
    assert depot.par_id(c["id"])["email"] == c["email"], f"{nom} : par_id"
    assert depot.par_email(c["email"])["id"] == c["id"], f"{nom} : par_email"
    assert depot.par_telephone(c["telephone"])["id"] == c["id"], f"{nom} : par_telephone"
    assert depot.par_id("inexistant") is None, f"{nom} : id inconnu doit donner None"
    assert depot.par_email("personne@nulle.part") is None, f"{nom} : e-mail inconnu"

    # ── Recherche par CLÉ, pas par valeur affichée ──
    assert depot.par_email(c["email"].upper())["id"] == c["id"], f"{nom} : casse"
    local, _, domaine = c["email"].partition("@")
    assert depot.par_email(f"{local}+alias@{domaine}")["id"] == c["id"], f"{nom} : alias +"
    international = "+33" + c["telephone"].lstrip("0")
    assert depot.par_telephone(international)["id"] == c["id"], f"{nom} : tel +33"

    # ── Unicité tenue par le MOTEUR ──
    doublon = _compte(2)
    doublon["email_cle"] = cle_email(c["email"])
    for champ in ("email", "telephone"):
        essai = dict(doublon)
        essai["id"] = secrets.token_hex(16)
        essai["email_cle"] = cle_email(c["email"]) if champ == "email" else cle_email(essai["email"])
        essai["telephone_cle"] = cle_telephone(c["telephone"]) if champ == "telephone" else cle_telephone(essai["telephone"])
        try:
            depot.creer(essai)
            raise AssertionError(f"{nom} : doublon de {champ} accepté")
        except ContactDejaUtilise:
            pass

    # ── Modification ──
    jusqua = time.time() + 365 * 86400
    depot.modifier(c["id"], formule="annuel", abonne_jusqua=jusqua, etat="PRO_ACTIVE")
    relu = depot.par_id(c["id"])
    assert relu["formule"] == "annuel", f"{nom} : modifier(formule)"
    assert abs(float(relu["abonne_jusqua"]) - jusqua) < 1, f"{nom} : modifier(abonne_jusqua)"
    depot.modifier(c["id"])                       # sans champ : ne doit pas lever

    # ── Sessions ──
    jeton = secrets.token_urlsafe(32)
    depot.creer_session(jeton, c["id"], 3600)
    assert depot.session(jeton)["compte_id"] == c["id"], f"{nom} : session"
    assert depot.session("jeton-inconnu") is None, f"{nom} : jeton inconnu"

    expire = secrets.token_urlsafe(32)
    depot.creer_session(expire, c["id"], -10)     # déjà périmée
    assert depot.session(expire) is None, f"{nom} : session périmée encore valide"

    depot.supprimer_session(jeton)
    assert depot.session(jeton) is None, f"{nom} : supprimer_session"

    a, b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    depot.creer_session(a, c["id"], 3600)
    depot.creer_session(b, c["id"], 3600)
    depot.supprimer_sessions_du_compte(c["id"])
    assert depot.session(a) is None and depot.session(b) is None, \
        f"{nom} : supprimer_sessions_du_compte"

    # ── Comptage ──
    avant = depot.nombre()
    depot.creer(_compte(3))
    assert depot.nombre() == avant + 1, f"{nom} : nombre()"


def test_contrat_sqlite():
    import importlib
    dossier = Path(tempfile.mkdtemp())
    os.environ["AGENCE_DATA_DIR"] = str(dossier / "data")
    import config as app_cfg
    app_cfg.DB_PATH = dossier / "data" / "agence.db"
    app_cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import utils.database as database
    importlib.reload(database)

    from licence.comptes import DepotComptes
    _verifier_contrat(DepotComptes(), "SQLite")
    print("  OK — contrat respecté par le dépôt SQLite")


def _dsn_de_test() -> str:
    return (os.getenv("AGENCE_TEST_PG_DSN", "") or "").strip()


def test_contrat_postgres():
    dsn = _dsn_de_test()
    if not dsn:
        print("  — PostgreSQL non testé (AGENCE_TEST_PG_DSN absente)")
        return
    try:
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
    except ImportError:
        print("  — PostgreSQL non testé (pilote absent)")
        return

    from licence.depot_postgres import DepotPostgres
    depot = DepotPostgres(dsn)
    try:
        # Table neuve : le contrat parle de comptages absolus.
        with depot._connexion() as conn:
            depot._assurer_tables(conn)
            with conn.cursor() as cur:
                cur.execute("TRUNCATE comptes, comptes_sessions")
            conn.commit()
        _verifier_contrat(depot, "PostgreSQL")
        diag = depot.diagnostic()
        assert diag["moteur"] == "postgresql" and diag["joignable"] is True
        # Le diagnostic ne doit jamais laisser filtrer les identifiants.
        assert "motdepasse" not in str(diag).lower() and "password" not in str(diag).lower()
        print("  OK — contrat respecté par le dépôt PostgreSQL (base réelle)")
    finally:
        depot.fermer()


def test_dsn_ipv6_doit_etre_entre_crochets():
    """Le piège numéro un de la centralisation.

    `urlsplit` ne lève RIEN sur une IPv6 nue : il lit « 2001 » comme hôte. La
    configuration paraît acceptée, puis la connexion échoue plus tard sur un
    message sans rapport. On tranche donc avant toute connexion.
    """
    from licence.depot_postgres import preparer_dsn
    nues = [
        "postgresql://u:p@2001:41d0:301::21:5432/agence",
        "postgresql://u:p@2001:41d0:301::21/agence",
        "postgresql://u:p@fe80::1:5432/agence",
    ]
    for dsn in nues:
        try:
            preparer_dsn(dsn)
            raise AssertionError(f"IPv6 sans crochets acceptée : {dsn}")
        except ValueError as e:
            assert "crochets" in str(e), e

    # Avec crochets : accepté, et le port reste facultatif.
    for dsn in ("postgresql://u:p@[2001:41d0:301::21]:5432/agence",
                "postgresql://u:p@[2001:41d0:301::21]/agence"):
        assert "[2001:41d0:301::21]" in preparer_dsn(dsn)

    # Les formes classiques ne sont pas prises pour des IPv6.
    for dsn in ("postgresql://u:p@51.38.1.2:5432/agence",
                "postgresql://u:p@db.exemple.fr:5432/agence",
                "postgresql://u:p@db.exemple.fr/agence"):
        preparer_dsn(dsn)
    print("  OK — IPv6 sans crochets refusée avec la correction, formes valides acceptées")


def test_tls_impose_vers_un_hote_distant():
    """La base transporte des mots de passe hachés et des jetons de session :
    en clair sur le réseau, ce serait toute la base des comptes exposée."""
    from licence.depot_postgres import preparer_dsn

    distant = "postgresql://u:p@[2001:41d0:301::21]:5432/agence"
    assert "sslmode=require" in preparer_dsn(distant), "TLS non imposé vers un hôte distant"
    assert "sslmode=require" in preparer_dsn("postgresql://u:p@db.exemple.fr/agence")

    # Un choix EXPLICITE plus strict est respecté.
    strict = distant + "?sslmode=verify-full"
    assert preparer_dsn(strict).count("sslmode") == 1 and "verify-full" in preparer_dsn(strict)

    # Désactiver TLS vers un hôte distant est REFUSÉ.
    try:
        preparer_dsn(distant + "?sslmode=disable")
        raise AssertionError("sslmode=disable accepté vers un hôte distant")
    except ValueError as e:
        assert "clair" in str(e) or "refusé" in str(e), e

    # En local, rien n'est imposé : la connexion ne quitte pas la machine.
    local = preparer_dsn("postgresql://u:p@127.0.0.1:5432/agence")
    assert "sslmode" not in local
    print("  OK — TLS imposé vers un hôte distant, sslmode=disable refusé")


def test_dsn_invalide_refuse_clairement():
    from licence.depot_postgres import preparer_dsn
    for dsn, attendu in [("", "vide"),
                         ("mysql://u:p@h/agence", "postgresql"),
                         ("postgresql:///agence", "hôte")]:
        try:
            preparer_dsn(dsn)
            raise AssertionError(f"DSN accepté à tort : {dsn!r}")
        except ValueError as e:
            assert attendu in str(e).lower(), f"{dsn!r} : message peu clair — {e}"
    print("  OK — DSN vide, mauvais moteur ou sans hôte : refusés avec explication")


def test_pas_de_repli_silencieux_sur_sqlite():
    """Si la base centrale est configurée mais injoignable, on NE retombe PAS
    sur SQLite : ce serait créer en silence un second jeu de comptes, avec des
    essais de 3 jours neufs et des abonnements introuvables."""
    from licence import comptes
    ancien = os.environ.get("AGENCE_COMPTES_DSN")
    try:
        # Adresse de documentation (RFC 5737) : elle ne répond jamais.
        os.environ["AGENCE_COMPTES_DSN"] = \
            "postgresql://u:p@192.0.2.1:5432/agence?connect_timeout=1"
        comptes.reinitialiser_depot()
        from licence.depot_postgres import DepotPostgres
        depot = comptes.depot()
        assert isinstance(depot, DepotPostgres), \
            "repli silencieux sur SQLite : les comptes seraient dédoublés"
    finally:
        if ancien is None:
            os.environ.pop("AGENCE_COMPTES_DSN", None)
        else:
            os.environ["AGENCE_COMPTES_DSN"] = ancien
        comptes.reinitialiser_depot()
    print("  OK — base centrale injoignable : pas de repli silencieux sur SQLite")


def test_stripe_ne_joint_pas_un_serveur_ipv6_seul():
    """Rappel documenté : Stripe ne gère QUE l'IPv4.

    Un serveur joignable uniquement en IPv6 ne recevra donc jamais de webhook,
    et l'application ne pourra pas joindre api.stripe.com. La base des comptes
    en IPv6 ne pose aucun problème (c'est du serveur à serveur) ; le paiement,
    si. Le guide doit le dire, sinon la panne est incompréhensible.
    """
    guide = (_RACINE / "DEPLOIEMENT.md").read_text(encoding="utf-8")
    assert "IPv6" in guide, "DEPLOIEMENT.md ne parle pas d'IPv6"
    bas = guide.lower()
    assert "ipv4" in bas, "le guide n'indique pas que l'IPv4 est nécessaire"
    assert "docs.stripe.com/ips" in guide, "le guide ne cite pas la source Stripe"
    print("  OK — le guide avertit que Stripe exige l'IPv4")


def run():
    print("═══ Dépôt de comptes : SQLite et PostgreSQL ═══")
    from utils import app_config
    import config as app_cfg
    _etat0 = (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
              os.environ.get("AGENCE_DATA_DIR"),
              os.environ.get("AGENCE_COMPTES_DSN"))
    try:
        test_contrat_sqlite()
        test_contrat_postgres()
        test_dsn_ipv6_doit_etre_entre_crochets()
        test_tls_impose_vers_un_hote_distant()
        test_dsn_invalide_refuse_clairement()
        test_pas_de_repli_silencieux_sur_sqlite()
        test_stripe_ne_joint_pas_un_serveur_ipv6_seul()
    finally:
        import importlib
        (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
         _data, _dsn) = _etat0
        for nom, valeur in (("AGENCE_DATA_DIR", _data),
                            ("AGENCE_COMPTES_DSN", _dsn)):
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur
        import utils.database as database
        importlib.reload(database)
        from licence import comptes
        comptes.reinitialiser_depot()


if __name__ == "__main__":
    run()
    print("✅ Dépôt de comptes OK")
