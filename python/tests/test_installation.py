"""Tests de l'empaquetage Windows : version, chemins après installation,
script d'installation Inno Setup et socle de mise à jour.

Ces tests défendent des invariants qui ne se voient QU'APRÈS installation,
c'est-à-dire là où personne ne les vérifie jamais avant que l'utilisateur ne
s'en plaigne :

- la version affichée par l'application, par l'exe et par « Applications
  installées » vient d'une source unique ;
- les données personnelles (historique de trading) sont écrites HORS du
  dossier d'installation — sinon la première mise à jour les efface ;
- l'installeur crée bien les raccourcis promis et ne supprime rien sans
  demander ;
- la compilation n'embarque pas la base de données de la machine de build.

Aucun test ne compile quoi que ce soit et aucun n'ouvre de connexion : ils
lisent les fichiers du dépôt et les modules Python.
"""
import _setup  # noqa: F401
import importlib
import os
import struct
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
ISS = RACINE / "installer" / "agence.iss"


def _lire_iss() -> str:
    return ISS.read_text(encoding="utf-8-sig")


# ── Version : une seule source ────────────────────────────────────────
def test_version_source_unique():
    """Le fichier VERSION fait autorité pour l'application ET pour l'installeur."""
    from utils.version import version

    fichier = RACINE / "VERSION"
    assert fichier.is_file(), "le fichier VERSION de la racine a disparu"
    attendue = fichier.read_text(encoding="utf-8").strip()
    assert attendue, "le fichier VERSION est vide"
    assert version() == attendue

    # Le backend annonce la MÊME version (/api/health, /api/diagnostic).
    from backend.main import APP_VERSION
    assert APP_VERSION == attendue, (
        f"le backend annonce {APP_VERSION} alors que VERSION dit {attendue}")

    # Et l'installeur la lit dans ce même fichier, au lieu de la recopier.
    iss = _lire_iss()
    assert '#define VersionFile SourcePath + "..\\VERSION"' in iss, (
        "l'installeur ne lit plus le fichier VERSION du dépôt, ou le cherche "
        "relativement au dossier courant de ISCC plutôt qu'au script")
    assert "FileRead" in iss, "l'installeur ne lit plus le fichier VERSION"
    assert f'AppVersion={attendue}' not in iss, (
        "la version est recopiée en dur dans agence.iss — elle divergera")


def test_version_comparee_numeriquement():
    """« 4.10.0 » est PLUS RÉCENTE que « 4.9.0 ».

    Comparées comme des chaînes, ces deux versions s'ordonnent à l'envers : la
    mise à jour serait manquée à partir de la dixième, et seulement à partir
    de là — le genre de bug qui n'apparaît que des mois après la mise en
    service.
    """
    from utils.version import tuple_version

    assert tuple_version("4.10.0") > tuple_version("4.9.0")
    assert tuple_version("5.0.0") > tuple_version("4.99.99")
    assert tuple_version("4.0.1") > tuple_version("4.0.0")
    assert tuple_version("4.0.0") == tuple_version("4.0.0")
    # Une version illisible ne doit pas faire exploser la comparaison.
    assert tuple_version("") == (0, 0, 0)
    assert tuple_version("4.0.0-rc1") == (4, 0, 0)


def test_version_windows_a_quatre_nombres():
    """La ressource « version » d'un exe Windows exige a.b.c.d."""
    from utils.version import version_windows

    morceaux = version_windows().split(".")
    assert len(morceaux) == 4
    assert all(m.isdigit() for m in morceaux)


# ── Chemins après installation ────────────────────────────────────────
def test_base_de_donnees_hors_du_dossier_installe():
    """Installée, l'application n'écrit PAS sa base dans son propre dossier.

    C'est l'invariant le plus coûteux de tous : l'installeur remplace
    intégralement le dossier d'installation à chaque mise à jour, et le
    désinstalleur l'efface. Une base rangée là disparaissait avec tout
    l'historique de trading, en silence, dès la première mise à jour.
    """
    import config

    faux_home = tempfile.mkdtemp(prefix="agence_home_")
    etait_frozen = getattr(sys, "frozen", None)
    executable0 = sys.executable
    env0 = {c: os.environ.get(c) for c in ("HOME", "USERPROFILE", "AGENCE_DATA_DIR")}
    try:
        os.environ.pop("AGENCE_DATA_DIR", None)
        os.environ["HOME"] = faux_home
        os.environ["USERPROFILE"] = faux_home
        sys.frozen = True                       # simule l'application compilée
        sys.executable = os.path.join(faux_home, "Programs", "AgenceNumerique",
                                      "AgenceNumerique.exe")
        importlib.reload(config)

        attendu = Path(faux_home) / ".agence_financiere" / "data"
        assert config.DB_PATH.parent == attendu, (
            f"base compilée dans {config.DB_PATH}, hors de {attendu}")
        # …et surtout pas à côté de l'exécutable.
        assert Path(sys.executable).parent not in config.DB_PATH.parents
    finally:
        if etait_frozen is None:
            del sys.frozen
        else:
            sys.frozen = etait_frozen
        sys.executable = executable0
        for cle, valeur in env0.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        importlib.reload(config)


def test_dossier_de_donnees_forcable():
    """AGENCE_DATA_DIR l'emporte — installation sur clé USB, tests, poste partagé."""
    import config

    cible = tempfile.mkdtemp(prefix="agence_data_")
    ancien = os.environ.get("AGENCE_DATA_DIR")
    try:
        os.environ["AGENCE_DATA_DIR"] = cible
        importlib.reload(config)
        assert config.DB_PATH.parent == Path(cible)
    finally:
        if ancien is None:
            os.environ.pop("AGENCE_DATA_DIR", None)
        else:
            os.environ["AGENCE_DATA_DIR"] = ancien
        importlib.reload(config)


def test_compilation_nembarque_pas_les_donnees_locales():
    """La base et le .env de la machine de compilation ne partent pas chez l'utilisateur.

    `datas += [("python", "python")]` copiait le dossier ENTIER, donc
    `python/data/agence.db` — la base créée dès qu'on lance l'application ou
    la suite de tests en local. L'installeur distribuait alors l'historique de
    trading du développeur, et le premier écran de l'utilisateur affichait des
    positions qui n'étaient pas les siennes.
    """
    spec = (RACINE / "agence.spec").read_text(encoding="utf-8")
    # La ligne de code, pas le commentaire qui l'explique.
    assert 'datas += [("python", "python")]' not in spec, (
        "l'arbre python/ est de nouveau copié en bloc : la base locale "
        "et le .env repartiraient dans l'installeur")
    assert "_arbre_python" in spec
    for exclu in ('"data"', '"__pycache__"', '".env"', '".db"'):
        assert exclu in spec, f"{exclu} n'est plus exclu de l'empaquetage"


# ── Script d'installation ─────────────────────────────────────────────
def test_installeur_present_et_complet():
    """L'assistant propose tout ce qu'un installeur Windows doit proposer."""
    assert ISS.is_file(), "installer/agence.iss a disparu"
    iss = _lire_iss()

    # Assistant graphique, nom et version affichés
    assert "WizardStyle=modern" in iss
    assert "AppName={#AppName}" in iss
    assert "AppVersion={#AppVersion}" in iss
    # Choix du dossier d'installation (la page NE DOIT PAS être désactivée)
    assert "DisableDirPage=no" in iss
    assert "DefaultDirName=" in iss
    # Raccourci Bureau (optionnel) et entrée dans le menu Démarrer
    assert "{autodesktop}\\{#AppName}" in iss
    assert "{group}\\{#AppName}" in iss
    assert "Tasks: desktopicon" in iss
    # Désinstallation enregistrée dans Windows
    assert "UninstallDisplayIcon=" in iss
    assert "{cm:UninstallProgram,{#AppName}}" in iss


def test_installeur_ne_supprime_pas_les_donnees_sans_demander():
    """La désinstallation efface l'application, jamais l'historique en silence.

    Les données personnelles vivent dans %USERPROFILE%\\.agence_financiere :
    historique de trading, réglages, identifiants du courtier et plusieurs
    gigaoctets de modèles d'IA. Les supprimer sans le demander, c'est
    transformer une désinstallation d'essai en perte définitive.
    """
    iss = _lire_iss()

    # Le nettoyage automatique ne porte QUE sur le dossier d'installation.
    lignes_suppression = [l.strip() for l in iss.splitlines()
                          if l.strip().startswith("Type:")]
    assert lignes_suppression, "plus aucune règle [UninstallDelete]"
    for ligne in lignes_suppression:
        assert 'Name: "{app}"' in ligne, (
            f"règle de suppression trop large : {ligne}")

    # Les données ne partent que derrière une question explicite, dont la
    # réponse par défaut est « Non ».
    assert "DelTree(Donnees" in iss
    assert "MB_DEFBUTTON2" in iss, "la réponse par défaut n'est plus « Non »"
    assert "cm:SupprimerDonnees" in iss
    # Une désinstallation silencieuse ne pose pas de question : elle conserve.
    assert "UninstallSilent()" in iss


def test_installeur_prepare_les_mises_a_jour():
    """Une nouvelle version s'installe PAR-DESSUS l'ancienne."""
    iss = _lire_iss()

    # AppId stable : sans lui, chaque version apparaît une fois de plus dans
    # « Applications installées », et aucune ne désinstalle les autres.
    assert "#define AppId" in iss
    assert "AppId={{#AppId}" in iss
    # Repères pour un futur programme de mise à jour : où, et quelle version.
    assert 'ValueName: "InstallPath"' in iss
    assert 'ValueName: "Version"' in iss
    # Fichiers verrouillés par l'application en cours d'exécution.
    assert "CloseApplications=yes" in iss


def test_installeur_gere_la_dependance_webview2():
    """WebView2 — la seule dépendance système qui puisse manquer — est traitée.

    La fenêtre de l'application repose sur le « Microsoft Edge WebView2
    Runtime ». Il est là d'origine sur Windows 11, mais pas forcément sur un
    Windows 10 fraîchement installé : sans lui, l'exe se lançait et
    disparaissait sans un mot.
    """
    iss = _lire_iss()
    assert "F3017226-FE2A-4295-8BDF-00C3A9A7E4C5" in iss, (
        "la détection du composant WebView2 a disparu")
    assert "/silent /install" in iss, "l'amorceur WebView2 n'est plus exécuté"
    assert "CreateDownloadPage" in iss, (
        "plus de repli par téléchargement quand l'amorceur n'est pas embarqué")
    # « L'amorceur est-il livré dans cet installeur ? » se décide À LA
    # COMPILATION. Répondre en cherchant le fichier dans {tmp} donnait toujours
    # « non » à la page « Prêt à installer » — les fichiers n'y sont pas encore
    # extraits — et faisait retélécharger un amorceur pourtant embarqué, y
    # compris sur une machine hors ligne.
    assert "AmorceurEmbarque()" in iss and "#ifdef WebView2Embarque" in iss
    assert "FileExists(ExpandConstant('{tmp}\\MicrosoftEdgeWebview2Setup.exe'))" not in iss

    # Et si tout échoue, l'application doit quand même s'ouvrir.
    app_window = (RACINE / "app_window.py").read_text(encoding="utf-8")
    assert "_ouvrir_dans_le_navigateur" in app_window
    assert "try:\n        webview.start(" in app_window, (
        "webview.start() n'est plus protégé : sans WebView2, l'application "
        "se fermerait sans rien afficher")


def test_installeur_reference_des_fichiers_existants():
    """Un fichier référencé mais absent ne se voit qu'au moment de compiler."""
    iss = _lire_iss()
    for nom in ("agence.ico", "infos_avant.txt", "infos_apres.txt"):
        assert nom in iss, f"{nom} n'est plus référencé par l'installeur"
        assert (ISS.parent / nom).is_file(), f"installer/{nom} est absent du dépôt"

    # La charge utile est bien le dossier produit par PyInstaller.
    assert '#define SourceApp' in iss
    assert 'Source: "{#SourceApp}\\*"' in iss


def test_icone_multi_resolutions():
    """L'icône couvre de 16 à 256 pixels.

    Windows pioche une taille différente selon l'endroit (barre des tâches,
    bureau, « très grandes icônes », assistant d'installation). Une icône qui
    n'en contient qu'une est floue partout ailleurs.
    """
    icone = ISS.parent / "agence.ico"
    brut = icone.read_bytes()
    reserve, type_image, nb = struct.unpack("<HHH", brut[:6])
    assert (reserve, type_image) == (0, 1), "ce fichier n'est pas un .ico"
    tailles = set()
    for i in range(nb):
        largeur = brut[6 + i * 16]
        tailles.add(largeur or 256)          # 0 signifie 256 dans le format ICO
    assert 256 in tailles, f"pas de 256×256 dans l'icône ({sorted(tailles)})"
    assert 16 in tailles, f"pas de 16×16 dans l'icône ({sorted(tailles)})"


def test_scripts_bat_lisibles_par_cmd():
    """Les .bat sont en CRLF, et leurs « goto » pointent vers des étiquettes réelles.

    Deux façons de fermer une fenêtre de terminal INSTANTANÉMENT, sans le
    moindre message, et donc sans rien à diagnostiquer :

    1. des fins de ligne Unix (LF). cmd.exe exige CRLF : sur un bloc
       « if (...) » multi-ligne il part en erreur de syntaxe et rend la main
       aussitôt. Tous les .bat de ce dépôt étaient dans ce cas — ils sont
       écrits depuis Linux, et rien ne le signalait ;
    2. un « goto :etiquette » mal orthographié. cmd.exe abandonne le script
       sur-le-champ.

    `.gitattributes` force désormais la conversion à l'extraction du dépôt ;
    ce test protège les fichiers eux-mêmes.
    """
    import re

    bats = sorted(list(RACINE.glob("*.bat")) + list((RACINE / "scripts").glob("*.bat")))
    assert bats, "plus aucun script .bat dans le dépôt"

    for chemin in bats:
        brut = chemin.read_bytes()
        assert brut.count(b"\r\n") == brut.count(b"\n"), (
            f"{chemin.name} contient des fins de ligne Unix : cmd.exe fermera "
            f"la fenêtre sur une erreur de syntaxe, sans message")

        texte = brut.decode("utf-8", errors="replace")
        etiquettes = {m.lower() for m in re.findall(r"(?m)^\s*:(\w+)", texte)}
        for cible in re.findall(r"(?i)\b(?:goto|call)\s+:(\w+)", texte):
            if cible.lower() == "eof":
                continue                       # étiquette intégrée à cmd.exe
            assert cible.lower() in etiquettes, (
                f"{chemin.name} saute vers « :{cible} », qui n'existe pas — "
                f"cmd.exe abandonne le script sans rien afficher")

    # La chaîne de compilation reste en ASCII pur : cmd.exe lit un fichier
    # UTF-8 sans BOM comme de l'ANSI, et abîme tout ce qui est accentué.
    for nom in ("build_exe.bat", "build_installer.bat", "scripts/build_installer.bat"):
        contenu = (RACINE / nom).read_bytes()
        assert all(o < 128 for o in contenu), f"{nom} contient des caractères non-ASCII"

    # Piège classique : dans « if cond a && b », cmd.exe rattache le « && » à
    # l'instruction IF, qui réussit même quand sa condition est fausse — donc
    # « b » s'exécute dans tous les cas.
    build = (RACINE / "build_exe.bat").read_text(encoding="ascii")
    for ligne in build.splitlines():
        nu = ligne.strip().lower()
        if nu.startswith("if ") or nu.startswith("if("):
            assert "&&" not in ligne, f"« if ... && » ambigu : {ligne.strip()}"


def test_build_exe_ne_se_ferme_jamais_sans_expliquer():
    """Chaque échec de compilation laisse la fenêtre ouverte avec son motif.

    Sans cela, l'utilisateur voit une fenêtre s'ouvrir puis disparaître, et
    n'a strictement aucun élément : ni la cause, ni où chercher.
    """
    build = (RACINE / "build_exe.bat").read_text(encoding="ascii")
    assert ":fin" in build and "pause" in build

    # Toute sortie en erreur rejoint :fin, qui affiche le motif puis attend.
    for etiquette in ("pas_de_python", "projet_incomplet", "echec_dependances",
                      "echec_compilation", "chemin_reseau"):
        assert f":{etiquette}" in build, f"cas d'erreur « {etiquette} » disparu"
    # …et chacun renseigne un motif lisible avant de partir sur :fin.
    assert build.count('set "MOTIF=') >= 6

    # AGENCE_BUILD_AUTO reste la seule facon de sauter le « pause » : sinon la
    # chaine automatisee (build_installer.bat, CI) resterait bloquee.
    assert "if not defined AGENCE_BUILD_AUTO pause" in build


def test_commande_unique_pour_tout_reconstruire():
    """Une seule commande régénère l'application ET son installeur."""
    for chemin in (RACINE / "build_installer.bat",
                   RACINE / "scripts" / "build_installer.bat"):
        assert chemin.is_file(), f"{chemin.name} a disparu"
    script = (RACINE / "scripts" / "build_installer.bat").read_text(encoding="utf-8")
    assert "build_exe.bat" in script, "la chaîne ne compile plus l'application"
    assert "agence.iss" in script, "la chaîne ne génère plus l'installeur"
    # Sans compilateur Inno Setup, le script doit dire quoi faire plutôt que
    # d'échouer sur une erreur incompréhensible.
    assert "jrsoftware.org" in script or "winget install" in script

    # build_exe.bat doit pouvoir tourner sans personne devant l'écran.
    build = (RACINE / "build_exe.bat").read_text(encoding="utf-8")
    assert "AGENCE_BUILD_AUTO" in build, (
        "build_exe.bat s'arrête toujours sur « pause » : la chaîne automatique "
        "resterait bloquée indéfiniment")


# ── Socle de mise à jour ──────────────────────────────────────────────
def test_verification_de_mise_a_jour_desactivee_par_defaut():
    """Sans URL configurée, AUCUNE connexion n'est ouverte.

    L'application se revendique 100 % locale : la vérification de mise à jour
    ne doit pas la faire sortir sur le réseau à l'insu de son propriétaire.
    """
    from utils import mise_a_jour

    ancien = os.environ.pop(mise_a_jour.VARIABLE_URL, None)
    appels = []
    original = mise_a_jour._telecharger_manifeste
    mise_a_jour._telecharger_manifeste = lambda url: appels.append(url) or {}
    try:
        etat = mise_a_jour.verifier()
        assert etat["actif"] is False
        assert etat["disponible"] is False
        assert appels == [], "une connexion a été tentée sans URL configurée"
    finally:
        mise_a_jour._telecharger_manifeste = original
        if ancien is not None:
            os.environ[mise_a_jour.VARIABLE_URL] = ancien


def test_mise_a_jour_detectee_et_jamais_bloquante():
    """Une version publiée plus récente est signalée ; une panne ne casse rien."""
    from utils import mise_a_jour

    original = mise_a_jour._telecharger_manifeste
    try:
        mise_a_jour._telecharger_manifeste = lambda url: {
            "version": "99.0.0", "url": "https://exemple.tld/setup.exe",
            "notes": "essai"}
        etat = mise_a_jour.verifier("https://exemple.tld/derniere.json")
        assert etat["actif"] and etat["disponible"]
        assert etat["version_publiee"] == "99.0.0"

        # Version identique ou plus ancienne : rien à proposer.
        mise_a_jour._telecharger_manifeste = lambda url: {"version": "0.0.1"}
        assert mise_a_jour.verifier("https://exemple.tld/x.json")["disponible"] is False

        # Serveur injoignable : l'application continue, avec la cause en clair.
        def _casse(url):
            raise OSError("serveur injoignable")
        mise_a_jour._telecharger_manifeste = _casse
        etat = mise_a_jour.verifier("https://exemple.tld/x.json")
        assert etat["disponible"] is False
        assert "erreur" in etat
    finally:
        mise_a_jour._telecharger_manifeste = original


def test_route_de_mise_a_jour_exposee():
    """L'application peut interroger son propre socle de mise à jour."""
    from backend.main import app

    chemins = {getattr(r, "path", "") for r in app.routes}
    assert "/api/mise-a-jour" in chemins
    # Sous /api/ : donc protégée par le middleware d'authentification.
    assert "/api/mise-a-jour".startswith("/api/")


def run():
    test_version_source_unique()
    test_version_comparee_numeriquement()
    test_version_windows_a_quatre_nombres()
    test_base_de_donnees_hors_du_dossier_installe()
    test_dossier_de_donnees_forcable()
    test_compilation_nembarque_pas_les_donnees_locales()
    test_installeur_present_et_complet()
    test_installeur_ne_supprime_pas_les_donnees_sans_demander()
    test_installeur_prepare_les_mises_a_jour()
    test_installeur_gere_la_dependance_webview2()
    test_installeur_reference_des_fichiers_existants()
    test_icone_multi_resolutions()
    test_scripts_bat_lisibles_par_cmd()
    test_build_exe_ne_se_ferme_jamais_sans_expliquer()
    test_commande_unique_pour_tout_reconstruire()
    test_verification_de_mise_a_jour_desactivee_par_defaut()
    test_mise_a_jour_detectee_et_jamais_bloquante()
    test_route_de_mise_a_jour_exposee()
    print("  ✓ 18 tests installation Windows / version / mises à jour")


if __name__ == "__main__":
    run()
    print("✅ Installation OK")
