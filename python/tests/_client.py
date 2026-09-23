"""Client HTTP de test, rattaché à un compte abonné.

POURQUOI
--------
L'application exige désormais un COMPTE : sans session ouverte, toutes les
routes répondent 401, et un compte dont l'essai est écoulé reçoit 402. Une
suite qui instancie `TestClient(app)` nu ne mesurerait donc plus que le mur
d'inscription.

Chaque client produit ici crée un compte RÉEL dans la base (temporaire) du
test, ouvre une vraie session, et lui pose des droits Pro par le même chemin
qu'un paiement confirmé. Rien n'est court-circuité : ni le middleware, ni la
porte du feature gate, ni la vérification de session en base.

Ce fichier vit dans `python/tests/`, que `agence.spec` exclut du paquet
distribué : cet outillage n'existe donc sur aucune machine d'utilisateur.
"""
import itertools
import secrets

_compteur = itertools.count(1)


def _identite() -> dict:
    """Contact unique à chaque appel.

    Indispensable : l'unicité de l'e-mail ET du téléphone est tenue par des
    index UNIQUE en base. Deux clients de test qui partageraient un contact se
    verraient refuser l'inscription — exactement comme un utilisateur qui
    tente de rouvrir un essai.
    """
    n = next(_compteur)
    hasard = secrets.token_hex(4)
    return {
        "email": f"test{n}.{hasard}@exemple-tests.invalid",
        "mot_de_passe": "motdepasse-de-test-1",
        # 9 chiffres significatifs, comme la clé d'unicité des téléphones.
        "telephone": f"06{n:03d}{int(hasard, 16) % 10000:04d}",
        "adresse": "1 rue des Tests",
        "code_postal": "75011",
        "ville": "Paris",
        "pays": "France",
    }


def creer_compte(abonne: bool = True) -> dict:
    """Crée un compte de test. `abonne` lui ouvre des droits Pro."""
    from licence import comptes
    compte = comptes.inscrire(_identite())
    if abonne:
        # Même fonction que celle appelée après confirmation Stripe — le
        # paiement lui-même n'est jamais simulé, seul son RÉSULTAT est posé.
        compte = comptes.activer_abonnement(compte["id"], "annuel",
                                            "test-suite")
    return compte


def client(app=None, abonne: bool = True, **kwargs):
    """`TestClient` déjà connecté sur un compte neuf.

    Renvoie le client ; le compte est accessible via `client.compte_agence`.
    """
    from fastapi.testclient import TestClient
    if app is None:
        from backend.main import app as application
        app = application

    kwargs.setdefault("raise_server_exceptions", False)
    c = TestClient(app, **kwargs)
    compte = creer_compte(abonne=abonne)

    from licence import comptes
    jeton = comptes.ouvrir_session(compte["id"])
    c.cookies.set(comptes.COOKIE_SESSION, jeton)
    c.compte_agence = compte
    return c
