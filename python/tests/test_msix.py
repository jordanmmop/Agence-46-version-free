"""Tests de l'empaquetage MSIX (Microsoft Store et installation hors Store).

Ces tests protègent une chaîne que PERSONNE ne peut vérifier sur cette
machine : construire un MSIX exige Windows et le SDK Windows. Tout ce qui
peut être contrôlé sans eux l'est donc ici, à chaque commit, plutôt qu'au
moment d'une publication :

- le manifeste se remplit depuis une source unique de version et d'identité ;
- toutes les icônes qu'il référence existent, aux dimensions exactes ;
- les scripts d'empaquetage sont lisibles par Windows PowerShell 5.1 ;
- le dépôt ne peut pas recevoir une clé privée ;
- le pipeline se déclenche là où il faut et ne contient aucun secret ;
- le conteneur MSIX (dossier d'installation en lecture seule) est pris en
  compte par l'application.

Aucun test ne compile quoi que ce soit et aucun n'ouvre de connexion.
"""
import _setup  # noqa: F401
import json
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
MSIX = RACINE / "packaging" / "msix"
GABARIT = MSIX / "AppxManifest.xml"
CONFIG = MSIX / "msix.config.json"
SCRIPTS = MSIX / "scripts"
WORKFLOW = RACINE / ".github" / "workflows" / "windows-msix.yml"

# Espaces de noms du manifeste : sans eux, ElementTree ne retrouve aucun
# élément et tous les tests passeraient en ne vérifiant rien.
NS = {
    "d": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}


def _config() -> dict:
    brut = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {k: v for k, v in brut.items() if not k.startswith("_")}


def _manifeste_rempli() -> str:
    """Rejoue en Python ce que fait New-ManifesteMsix (common.ps1).

    Le but n'est pas de tester PowerShell : c'est de vérifier que le GABARIT
    et la CONFIGURATION produisent ensemble un manifeste valide. Un jeton
    ajouté au gabarit sans clé correspondante dans la configuration casse la
    chaîne de publication ; ici, il casse ce test.
    """
    from utils.version import version_windows

    texte = GABARIT.read_text(encoding="utf-8")
    cfg = _config()
    valeurs = {
        "IDENTITY_NAME": cfg["IdentityName"],
        "PUBLISHER": cfg["Publisher"],
        "VERSION": version_windows(),
        "ARCHITECTURE": cfg["Architecture"],
        "DISPLAY_NAME": cfg["DisplayName"],
        "PUBLISHER_DISPLAY_NAME": cfg["PublisherDisplayName"],
        "DESCRIPTION": cfg["Description"],
        "APPLICATION_ID": cfg["ApplicationId"],
        "EXECUTABLE": cfg["Executable"],
        "BACKGROUND_COLOR": cfg["BackgroundColor"],
        "SHORT_NAME": cfg.get("ShortName", cfg["DisplayName"]),
        "MIN_WINDOWS_VERSION": cfg["MinWindowsVersion"],
        "MAX_VERSION_TESTED": cfg["MaxVersionTested"],
    }
    for cle, valeur in valeurs.items():
        texte = texte.replace("{{%s}}" % cle, valeur)
    restants = set(re.findall(r"\{\{(\w+)\}\}", texte))
    assert not restants, (
        f"jetons du gabarit sans valeur dans msix.config.json : {sorted(restants)} — "
        f"le manifeste publié contiendrait « {{{{...}}}} » comme nom d'application")
    return texte


def _dimensions_png(chemin: Path) -> tuple:
    """(largeur, hauteur) d'un PNG, sans Pillow.

    La suite de tests tourne sur un runner où seules les dépendances de
    `python/requirements.txt` sont installées — Pillow n'en fait pas partie,
    et ne doit pas en faire partie pour un simple contrôle d'en-tête.
    """
    brut = chemin.read_bytes()
    assert brut[:8] == b"\x89PNG\r\n\x1a\n", f"{chemin.name} n'est pas un PNG"
    assert brut[12:16] == b"IHDR", f"{chemin.name} : en-tête IHDR absent"
    largeur, hauteur = struct.unpack(">II", brut[16:24])
    return largeur, hauteur


# ── Structure et manifeste ────────────────────────────────────────────
def test_structure_de_packaging_presente():
    """Les fichiers sans lesquels aucun MSIX ne peut être construit."""
    assert GABARIT.is_file(), "gabarit AppxManifest.xml absent"
    assert CONFIG.is_file(), "msix.config.json absent"
    assert (MSIX / "Assets").is_dir(), "dossier des icônes absent"
    assert (MSIX / "README.md").is_file(), "packaging/msix/README.md absent"
    for nom in ("common.ps1", "build-msix.ps1", "sign-msix.ps1",
                "validate-msix.ps1", "install-test.ps1", "uninstall-test.ps1",
                "create-test-certificate.ps1"):
        assert (SCRIPTS / nom).is_file(), f"script manquant : {nom}"


def test_gabarit_ne_fige_aucune_valeur_variable():
    """Version, identité et éditeur restent des jetons.

    Recopiés en dur dans le gabarit, ces trois champs divergeraient du fichier
    VERSION et des valeurs Partner Center — et c'est précisément ce trio qui,
    incohérent, fait refuser un paquet sans que le message dise lequel est en
    cause.
    """
    texte = GABARIT.read_text(encoding="utf-8")
    for jeton in ("{{VERSION}}", "{{IDENTITY_NAME}}", "{{PUBLISHER}}",
                  "{{PUBLISHER_DISPLAY_NAME}}", "{{ARCHITECTURE}}"):
        assert jeton in texte, f"{jeton} n'est plus un jeton dans le gabarit"

    version = (RACINE / "VERSION").read_text(encoding="utf-8").strip()
    assert f'Version="{version}' not in texte, (
        "la version est écrite en dur dans le gabarit — elle divergera de VERSION")


def test_manifeste_rempli_est_valide():
    """Le manifeste produit est du XML valide et déclare ce qu'il faut."""
    xml = ET.fromstring(_manifeste_rempli())

    identite = xml.find("d:Identity", NS)
    assert identite is not None, "aucun bloc <Identity>"

    # Quatre nombres : un manifeste MSIX refuse « 4.0.0 ».
    version = identite.get("Version")
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version), (
        f"version MSIX « {version} » : quatre nombres attendus")
    for nombre in version.split("."):
        assert int(nombre) < 65536, f"composant de version hors bornes : {nombre}"

    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{2,49}", identite.get("Name")), (
        f"Identity Name invalide : {identite.get('Name')}")
    assert identite.get("Publisher").startswith("CN="), (
        "Publisher doit être un nom distinctif (CN=...)")
    assert identite.get("ProcessorArchitecture") in ("x64", "x86", "arm64", "neutral")

    app = xml.find("d:Applications/d:Application", NS)
    assert app is not None, "aucune <Application> déclarée"
    # Une application Win32 empaquetée DOIT annoncer ce point d'entrée : sinon
    # Windows la traite comme une application UWP et la tue au démarrage.
    assert app.get("EntryPoint") == "Windows.FullTrustApplication"
    assert app.get("Executable"), "aucun exécutable déclaré"


def test_version_msix_vient_du_fichier_version():
    """La version du paquet est celle de l'application, révision à zéro.

    Trois numéros circulent déjà (application, exe compilé, installeur Inno
    Setup) et ils viennent tous du fichier VERSION. Le MSIX doit rejoindre la
    même source, sans quoi le Store afficherait une version et l'application
    une autre.
    """
    from utils.version import version, version_windows

    fichier = (RACINE / "VERSION").read_text(encoding="utf-8").strip()
    assert version() == fichier
    assert version_windows().startswith(fichier.split("-")[0])

    xml = ET.fromstring(_manifeste_rempli())
    assert xml.find("d:Identity", NS).get("Version") == version_windows()

    # Le Store se RÉSERVE la révision : un paquet soumis doit la laisser à 0.
    assert version_windows().endswith(".0"), (
        "la révision doit rester 0 : le Microsoft Store refuse les autres")


def test_capacites_declarees_minimales():
    """runFullTrust est indispensable, et le reste doit rester justifiable.

    Chaque capacité superflue allonge la revue du Store et s'affiche à
    l'utilisateur au moment de l'installation. On vérifie donc les deux sens :
    la capacité obligatoire est là, et aucune capacité large ne s'est glissée
    dans le manifeste.
    """
    xml = ET.fromstring(_manifeste_rempli())
    capacites = xml.find("d:Capabilities", NS)
    assert capacites is not None, "aucun bloc <Capabilities>"

    noms = {c.get("Name") for c in capacites}
    assert "runFullTrust" in noms, (
        "runFullTrust absente : Windows tuerait l'application au démarrage")

    autorisees = {"runFullTrust", "internetClient", "internetClientServer",
                  "privateNetworkClientServer"}
    superflues = noms - autorisees
    assert not superflues, (
        f"capacités non justifiées dans le manifeste : {sorted(superflues)}")


# ── Identité et Partner Center ────────────────────────────────────────
def test_identite_provisoire_clairement_marquee():
    """Tant que Partner Center n'a rien fourni, l'identité doit dire TEST.

    Sans cette marque, rien ne distingue à l'œil un paquet publiable d'un
    paquet d'essai — et on découvre l'erreur après avoir téléversé plusieurs
    centaines de mégaoctets.
    """
    cfg = _config()
    provisoire = "TEST" in cfg["IdentityName"] or "TEST" in cfg["Publisher"]
    if provisoire:
        assert "TEST" in cfg["IdentityName"] and "TEST" in cfg["Publisher"], (
            "identité à moitié provisoire : marquez les deux, ou aucun")
    # Le fichier doit expliquer quoi remplacer, sinon l'information se perd.
    brut = json.loads(CONFIG.read_text(encoding="utf-8"))
    documentation = " ".join(brut.get("_lisez-moi", []))
    assert "Partner Center" in documentation, (
        "msix.config.json n'explique plus d'où viennent les valeurs définitives")


def test_nom_editeur_distinct_du_nom_application():
    """PublisherDisplayName est le nom du COMPTE, pas celui de l'application.

    Régression d'une soumission réellement refusée par Partner Center :

        « L'élément PublisherDisplayName du manifeste de l'application de
          A46-4.0.0.0-x64.msix est Agence Numérique Financière, qui ne
          correspond pas à votre nom complet d'éditeur : Muller.J. »

    Le dépôt livrait les deux champs avec la même valeur — l'erreur ne se
    voyait qu'après avoir téléversé 87 Mo, et le message ne nomme que le champ
    fautif, pas les deux autres valeurs d'identité qu'il faut vérifier en même
    temps.
    """
    cfg = _config()
    assert cfg["PublisherDisplayName"] != cfg["DisplayName"], (
        "PublisherDisplayName vaut le nom de l'application : Partner Center "
        "attend ici le nom du compte d'éditeur, et refuse le paquet au "
        "téléversement")
    assert cfg["PublisherDisplayName"].strip(), "PublisherDisplayName vide"


def test_identite_partner_center_renseignee():
    """Les valeurs d'identité sont celles du compte, pas des remplaçantes.

    Relevées sur « Afficher l'identité du produit » : un nom d'identité
    préfixé par l'éditeur, et un Publisher en CN=<GUID>. Tant que ces deux
    champs portent des valeurs de développement, le paquet se construit, se
    signe, s'installe — et ne franchit jamais le téléversement.
    """
    cfg = _config()
    assert "TEST" not in cfg["IdentityName"], (
        "IdentityName porte encore une valeur de développement")
    assert re.fullmatch(r"CN=[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
                        r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", cfg["Publisher"]), (
        f"Publisher « {cfg['Publisher']} » n'est pas le CN=<GUID> fourni par "
        f"Partner Center")


def test_aucun_caractere_invisible_dans_lidentite():
    """Ni espace en trop, ni caractère parasite dans les champs d'identité.

    Régression d'un aller-retour réel : le nom d'éditeur avait été recopié
    depuis un message d'erreur français — « ...votre nom complet d'éditeur :
    Muller.J. » — dont le point final était la PONCTUATION de la phrase. Le
    manifeste portait donc « Muller.J. » au lieu de « Muller.J », et Partner
    Center affichait deux valeurs visuellement identiques dans le même
    message d'erreur.

    Une espace de fin produit exactement le même échec, en encore moins
    visible.
    """
    cfg = _config()
    for cle in ("IdentityName", "Publisher", "PublisherDisplayName", "DisplayName"):
        valeur = cfg[cle]
        assert valeur == valeur.strip(), (
            f"{cle} commence ou finit par une espace : « {valeur} »")
        assert "\u00a0" not in valeur, f"{cle} contient une espace insécable"

    # Partner Center construit le nom d'identité comme « <éditeur>.<app> ».
    # Le POINT de séparation compte dans ce test : sans lui, « Muller.J. »
    # resterait un préfixe valide de « Muller.J.Agence46 » et le point en trop
    # passerait inaperçu — c'est exactement ce qui a fait refuser le paquet.
    prefixe = cfg["PublisherDisplayName"] + "."
    assert cfg["IdentityName"].startswith(prefixe), (
        f"IdentityName « {cfg['IdentityName']} » ne commence pas par "
        f"« {prefixe} » — le nom d'éditeur porte probablement un caractère en "
        f"trop (un point final de ponctuation recopié depuis un message "
        f"d'erreur, par exemple)")


def test_validation_controle_les_trois_champs_didentite():
    """Le validateur affiche et contrôle ce que Partner Center confronte.

    Les trois champs sont vérifiés par le Store à des instants différents et
    le message d'erreur n'en nomme qu'un. Les afficher ensemble avant le
    téléversement remplace un aller-retour de plusieurs dizaines de mégaoctets.
    """
    script = (SCRIPTS / "validate-msix.ps1").read_text(encoding="ascii")
    assert "PublisherDisplayName" in script, (
        "le validateur ne contrôle plus le nom d'éditeur")
    assert "CN=[0-9A-Fa-f]{8}" in script, (
        "le validateur ne vérifie plus que Publisher est un CN=<GUID> Partner Center")
    assert "nom a reserver" in script, (
        "le validateur n'affiche plus le DisplayName à réserver dans Partner Center")


def test_identite_surchargeable_sans_modifier_le_depot():
    """Publier ne doit obliger à modifier AUCUN fichier versionné.

    Les valeurs Partner Center arrivent par variables d'environnement : c'est
    ce qui permet à la CI de produire un paquet de test et à une publication
    d'en produire un autre depuis le même commit.
    """
    common = (SCRIPTS / "common.ps1").read_text(encoding="utf-8")
    for variable in ("MSIX_IDENTITY_NAME", "MSIX_PUBLISHER",
                     "MSIX_PUBLISHER_DISPLAY_NAME"):
        assert variable in common, f"{variable} n'est plus prise en compte"

    brut = json.loads(CONFIG.read_text(encoding="utf-8"))
    documentation = " ".join(brut.get("_lisez-moi", []))
    assert "MSIX_IDENTITY_NAME" in documentation


# ── Icônes ────────────────────────────────────────────────────────────
def test_icones_referencees_existent_aux_bonnes_dimensions():
    """Chaque image citée par le manifeste existe, à la taille EXACTE.

    Windows ne redimensionne pas : il lit un fichier aux dimensions déclarées.
    Une image absente fait échouer `makeappx` ; une image à la mauvaise taille
    passe la compilation et donne une vignette vide dans le menu Démarrer.
    """
    texte = _manifeste_rempli()
    references = sorted(set(re.findall(r'"(Assets\\[^"]+\.png)"', texte)))
    assert references, "le manifeste ne référence plus aucune icône"

    # Dimensions imposées par Windows pour les noms normalisés.
    attendues = {
        "StoreLogo.png": (50, 50),
        "Square44x44Logo.png": (44, 44),
        "Square71x71Logo.png": (71, 71),
        "Square150x150Logo.png": (150, 150),
        "Square310x310Logo.png": (310, 310),
        "Wide310x150Logo.png": (310, 150),
        "SplashScreen.png": (620, 300),
    }
    for reference in references:
        chemin = MSIX / reference.replace("\\", "/")
        assert chemin.is_file(), (
            f"icône référencée mais absente : {reference} — "
            f"régénérez-les : python tools/generer_assets_msix.py")
        nom = chemin.name
        if nom in attendues:
            assert _dimensions_png(chemin) == attendues[nom], (
                f"{nom} : {_dimensions_png(chemin)} au lieu de {attendues[nom]}")


def test_variantes_barre_des_taches_presentes():
    """Les icônes « unplated » évitent une vignette minuscule dans la barre.

    Elles ne sont pas référencées par le manifeste : Windows les trouve par
    convention de nommage. Rien ne signalerait donc leur disparition.
    """
    assets = MSIX / "Assets"
    unplated = list(assets.glob("Square44x44Logo.altform-unplated_targetsize-*.png"))
    assert len(unplated) >= 4, (
        f"seulement {len(unplated)} variantes altform-unplated : "
        f"icône dégradée dans la barre des tâches et Alt-Tab")
    for chemin in unplated:
        taille = int(re.search(r"targetsize-(\d+)", chemin.name).group(1))
        assert _dimensions_png(chemin) == (taille, taille), (
            f"{chemin.name} ne fait pas {taille}x{taille}")


def test_generateur_dassets_couvre_le_manifeste():
    """Le générateur produit bien tout ce que le manifeste demande.

    Les images sont versionnées pour que la compilation ne dépende pas de
    Pillow ; le générateur ne sert qu'à les refaire. Encore faut-il qu'il ne
    se désynchronise pas du manifeste.
    """
    source = (RACINE / "tools" / "generer_assets_msix.py").read_text(encoding="utf-8")
    texte = _manifeste_rempli()
    for reference in set(re.findall(r'"Assets\\([^"]+\.png)"', texte)):
        assert f'"{reference}"' in source, (
            f"{reference} est référencée par le manifeste mais n'est pas "
            f"produite par tools/generer_assets_msix.py")


# ── Scripts d'empaquetage ─────────────────────────────────────────────
def test_scripts_powershell_lisibles_par_windows_powershell_51():
    """Les .ps1 restent en ASCII pur.

    Windows PowerShell 5.1 — celui qui est installé d'origine et que lance un
    double-clic — lit un .ps1 SANS BOM comme du texte ANSI. Le moindre
    accent y devient un caractère parasite, y compris dans les chaînes que le
    code compare. Même raison que les .bat du dépôt.
    """
    for chemin in sorted(SCRIPTS.glob("*.ps1")):
        brut = chemin.read_bytes()
        fautifs = sorted({o for o in brut if o > 127})
        assert not fautifs, (
            f"{chemin.name} contient des octets non-ASCII ({fautifs[:5]}) : "
            f"illisible par Windows PowerShell 5.1 sans BOM")


def test_scripts_powershell_signalent_leurs_echecs():
    """Un script d'empaquetage doit rendre un code non nul quand il échoue.

    Sans cela, la CI enchaîne sur l'étape suivante et publie une release à
    partir d'un paquet qui n'existe pas.
    """
    for nom in ("build-msix.ps1", "sign-msix.ps1", "validate-msix.ps1",
                "install-test.ps1"):
        texte = (SCRIPTS / nom).read_text(encoding="ascii")
        assert "exit 1" in texte, f"{nom} ne signale jamais d'échec"
        assert "$ErrorActionPreference" in texte or "common.ps1" in texte, (
            f"{nom} n'arrête pas le script sur erreur")


def test_scripts_ne_contiennent_aucun_secret():
    """Aucun mot de passe ni certificat en dur dans les scripts.

    Ils lisent tous des variables d'environnement ou des fichiers ignorés par
    git : c'est ce qui garantit qu'un `git add -A` distrait ne publie pas une
    clé privée.
    """
    suspects = re.compile(
        r"(password|motdepasse|mot_de_passe)\s*=\s*[\"'][^\"'$)\s]{6,}[\"']",
        re.IGNORECASE)
    for chemin in list(SCRIPTS.glob("*.ps1")) + [RACINE / "build_msix.bat"]:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
        trouve = suspects.findall(texte)
        assert not trouve, f"{chemin.name} : secret potentiellement en dur ({trouve})"
        assert "BEGIN PRIVATE KEY" not in texte
        assert "BEGIN RSA PRIVATE KEY" not in texte


def test_certificat_de_test_genere_jamais_versionne():
    """La clé privée de test est fabriquée à la demande, jamais stockée.

    Le script l'écrit sous dist/ (déjà ignoré) et le .gitignore refuse en plus
    toute extension de clé : deux barrières indépendantes, parce qu'une seule
    finit toujours par sauter.
    """
    script = (SCRIPTS / "create-test-certificate.ps1").read_text(encoding="ascii")
    assert "New-SelfSignedCertificate" in script, (
        "le certificat de test n'est plus généré localement")

    gitignore = (RACINE / ".gitignore").read_text(encoding="utf-8")
    for motif in ("*.pfx", "*.p12", "*.key", "*.pem"):
        assert motif in gitignore, f"{motif} n'est pas ignoré par git"

    # Et rien de tel n'est réellement présent dans l'arbre versionné.
    for extension in ("*.pfx", "*.p12", "*.key"):
        trouves = [p for p in RACINE.rglob(extension)
                   if ".git" not in p.parts and "dist" not in p.parts]
        assert not trouves, f"clé privée versionnée : {trouves}"


def test_sujet_du_certificat_lie_au_manifeste():
    """Le certificat de test tire son sujet du Publisher du manifeste.

    Les deux DOIVENT être identiques au caractère près. Recopier la valeur à
    la main est la cause n°1 d'un paquet signé que Windows refuse ensuite
    d'installer, avec un message qui ne cite ni l'un ni l'autre.
    """
    script = (SCRIPTS / "create-test-certificate.ps1").read_text(encoding="ascii")
    assert "Get-ConfigMsix" in script, (
        "le certificat de test n'utilise plus le Publisher du manifeste")
    assert "$config['Publisher']" in script or '$config["Publisher"]' in script

    signature = (SCRIPTS / "sign-msix.ps1").read_text(encoding="ascii")
    assert "ConvertTo-SujetNormalise" in signature, (
        "sign-msix.ps1 ne compare plus le sujet du certificat au manifeste")


def test_commande_unique_pour_construire_le_msix():
    """Un double-clic suffit, comme pour l'exe et l'installeur."""
    bat = RACINE / "build_msix.bat"
    assert bat.is_file(), "build_msix.bat a disparu"
    brut = bat.read_bytes()
    assert brut.count(b"\r\n") == brut.count(b"\n"), (
        "build_msix.bat contient des fins de ligne Unix : cmd.exe fermera la "
        "fenêtre sur une erreur de syntaxe, sans message")
    assert all(o < 128 for o in brut), "build_msix.bat contient des non-ASCII"
    texte = brut.decode("ascii")
    assert "build-msix.ps1" in texte
    assert "ExecutionPolicy Bypass" in texte, (
        "sans ce drapeau, Windows refuse d'exécuter le script et n'explique rien")


def test_build_msix_reutilise_la_chaine_existante():
    """Le MSIX emballe la sortie PyInstaller ; il ne recompile pas autrement.

    Deux chaînes de compilation parallèles divergeraient en quelques mois, et
    le paquet publié finirait par ne plus correspondre à l'exe testé.
    """
    build = (SCRIPTS / "build-msix.ps1").read_text(encoding="ascii")
    assert "build_exe.bat" in build, (
        "build-msix.ps1 n'appelle plus la chaîne de compilation du dépôt")
    assert "AGENCE_BUILD_AUTO" in build, (
        "build_exe.bat s'arrêtera sur « pause » : la chaîne resterait bloquée")
    assert "AGENCE_SANS_RUNTIMES" in build, (
        "le paquet MSIX doit rester allégé (dossier d'installation en lecture seule)")
    assert "Get-VersionDepot" in build, (
        "la version du paquet ne vient plus du fichier VERSION")
    assert "SHA256" in build, "les empreintes ne sont plus calculées"
    assert "DryRun" in build, "le mode --dry-run a disparu"


# ── Conteneur MSIX vu par l'application ───────────────────────────────
def test_application_detecte_le_conteneur_msix():
    """L'application sait dire si elle tourne depuis un paquet."""
    from utils import msix

    ancien = os.environ.get("AGENCE_MSIX")
    try:
        os.environ["AGENCE_MSIX"] = "1"
        msix.reinitialiser()
        assert msix.empaquete() is True

        os.environ["AGENCE_MSIX"] = "0"
        msix.reinitialiser()
        assert msix.empaquete() is False

        etat = msix.resume()
        assert etat["empaquete"] is False
        # Les données restent au MÊME endroit, empaqueté ou non : c'est ce qui
        # permet de passer de l'installeur classique au MSIX sans rien perdre.
        assert etat["dossier_donnees"].endswith(".agence_financiere")
    finally:
        if ancien is None:
            os.environ.pop("AGENCE_MSIX", None)
        else:
            os.environ["AGENCE_MSIX"] = ancien
        msix.reinitialiser()


def test_terminal_portable_refuse_dans_un_paquet_en_lecture_seule():
    """Un MetaTrader portable embarqué dans le paquet est écarté.

    Le mode /portable écrit ses profils À CÔTÉ de son exécutable, or le
    dossier d'installation d'un MSIX est monté en lecture seule pour tout le
    monde. Le terminal démarrerait puis refuserait toute connexion — panne
    silencieuse, découverte à la première tentative de trading réel.
    """
    from utils import msix

    ancien_msix = os.environ.get("AGENCE_MSIX")
    etait_frozen = getattr(sys, "frozen", None)
    executable0 = sys.executable
    try:
        os.environ["AGENCE_MSIX"] = "1"
        msix.reinitialiser()
        sys.frozen = True
        paquet = Path(executable0).resolve().parent
        sys.executable = str(paquet / "AgenceNumerique.exe")

        dans_le_paquet = paquet / "runtime" / "mt5" / "terminal" / "terminal64.exe"
        assert msix.dans_le_paquet(dans_le_paquet) is True
        assert msix.terminal_portable_utilisable(dans_le_paquet) is False, (
            "un terminal portable dans le paquet serait lancé alors qu'il ne "
            "peut pas écrire : connexion impossible, sans message")

        # Hors du paquet (téléchargé dans ~/.agence_financiere), il reste
        # parfaitement utilisable : la garde ne doit rien casser d'autre.
        dehors = Path(os.path.expanduser("~")) / ".agence_financiere" / "mt5" / "terminal64.exe"
        assert msix.dans_le_paquet(dehors) is False
        assert msix.terminal_portable_utilisable(dehors) is True

        # Et hors MSIX, un terminal embarqué reste utilisable comme avant.
        os.environ["AGENCE_MSIX"] = "0"
        msix.reinitialiser()
        assert msix.terminal_portable_utilisable(dans_le_paquet) is True
    finally:
        if etait_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = etait_frozen
        sys.executable = executable0
        if ancien_msix is None:
            os.environ.pop("AGENCE_MSIX", None)
        else:
            os.environ["AGENCE_MSIX"] = ancien_msix
        msix.reinitialiser()


def test_garde_msix_appliquee_dans_mt5_installer():
    """La garde est REELLEMENT branchée dans le code qui cherche le terminal.

    Le test précédent vérifie la règle ; celui-ci vérifie qu'elle est appelée.
    Sans lui, un futur remaniement de `MT5Installer.terminal_embarque()`
    pourrait retirer l'appel à `msix.terminal_portable_utilisable()` sans
    qu'aucun test ne bronche — et l'on relancerait un terminal incapable
    d'écrire, dont l'échec ne se voit qu'à la première tentative de trading
    réel, chez l'utilisateur.
    """
    import tempfile
    from utils import msix
    from utils.mt5_installer import MT5Installer

    # Faux dossier d'installation contenant un terminal portable embarqué.
    paquet = Path(tempfile.mkdtemp(prefix="faux_msix_"))
    terminal = paquet / "runtime" / "mt5" / "terminal" / "terminal64.exe"
    terminal.parent.mkdir(parents=True)
    terminal.write_bytes(b"MZ")

    etait_frozen = getattr(sys, "frozen", None)
    executable0 = sys.executable
    env0 = {c: os.environ.get(c) for c in ("AGENCE_MSIX", "AGENCE_RUNTIME_DIR")}
    try:
        sys.frozen = True
        sys.executable = str(paquet / "AgenceNumerique.exe")
        os.environ["AGENCE_RUNTIME_DIR"] = str(paquet / "runtime")

        # Hors MSIX (installeur Inno Setup) : le terminal embarqué sert, comme
        # avant. La garde ne doit RIEN changer à ce cas.
        os.environ["AGENCE_MSIX"] = "0"
        msix.reinitialiser()
        assert MT5Installer.terminal_embarque() is not None, (
            "le terminal embarqué n'est plus utilisé hors MSIX — régression")

        # Dans un paquet MSIX : écarté, sans quoi il démarrerait puis
        # refuserait toute connexion faute de pouvoir écrire.
        os.environ["AGENCE_MSIX"] = "1"
        msix.reinitialiser()
        assert MT5Installer.terminal_embarque() is None, (
            "le terminal du paquet serait lancé malgré la lecture seule")
    finally:
        if etait_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = etait_frozen
        sys.executable = executable0
        for cle, valeur in env0.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        msix.reinitialiser()


def test_diagnostic_expose_letat_du_paquet():
    """/api/diagnostic distingue une installation MSIX d'une installation classique.

    Sans cette information, deux utilisateurs aux symptômes identiques
    rapporteraient le même diagnostic alors qu'un seul des deux peut utiliser
    un terminal MetaTrader embarqué.
    """
    source = (RACINE / "backend" / "main.py").read_text(encoding="utf-8")
    assert '_bloc("empaquetage"' in source, (
        "le diagnostic n'expose plus l'état de l'empaquetage MSIX")


def test_donnees_utilisateur_hors_du_paquet():
    """Aucune écriture dans le dossier d'installation, qui est en lecture seule.

    C'est l'invariant qui rend cette application empaquetable sans la
    réécrire : configuration, base de trading et modèles d'IA vivent déjà tous
    dans ~/.agence_financiere.
    """
    from utils import app_config

    assert ".agence_financiere" in str(app_config._PATH)
    assert str(app_config._PATH).startswith(os.path.expanduser("~"))

    for module, attendu in (("utils.ollama_embedded", ".agence_financiere"),
                            ("utils.hermes_embedded", ".agence_financiere"),
                            ("utils.mt5_installer", ".agence_financiere")):
        mod = __import__(module, fromlist=["DOSSIER"])
        assert attendu in str(mod.DOSSIER), (
            f"{module}.DOSSIER écrit hors de ~/.agence_financiere : "
            f"{mod.DOSSIER} — impossible dans un paquet MSIX")


# ── Pipeline GitHub Actions ───────────────────────────────────────────
def test_workflow_msix_present_et_declenche_par_un_tag():
    """Le pipeline part sur « v1.2.3 » et tourne sur un runner Windows."""
    assert WORKFLOW.is_file(), ".github/workflows/windows-msix.yml absent"
    texte = WORKFLOW.read_text(encoding="utf-8")

    assert "windows-latest" in texte, (
        "makeappx, signtool et PyInstaller pour Windows exigent un runner Windows")
    assert re.search(r"tags:\s*\n\s*-\s*[\"']?v\*", texte), (
        "le workflow ne se déclenche plus sur les tags v*")
    assert "workflow_dispatch" in texte, (
        "impossible de lancer le pipeline à la main pour un essai")


def test_workflow_publie_paquet_et_empreintes():
    """Artifact, release et checksums : ce que la chaîne doit produire."""
    texte = WORKFLOW.read_text(encoding="utf-8")
    assert "upload-artifact" in texte, "le .msix n'est plus publié comme artefact"
    assert "SHA256SUMS.txt" in texte, "les empreintes ne sont plus publiées"
    assert "softprops/action-gh-release" in texte or "gh release" in texte, (
        "le paquet ne rejoint plus la GitHub Release")
    assert "validate-msix.ps1" in texte, (
        "le paquet est publié sans avoir été validé")


def test_workflow_ne_contient_aucun_secret_en_dur():
    """Les certificats passent par GitHub Secrets, jamais par le YAML.

    Et le pipeline doit fonctionner SANS ces secrets : produire un paquet de
    test ne doit jamais dépendre d'un certificat de production.
    """
    texte = WORKFLOW.read_text(encoding="utf-8")
    assert "MSIX_CERTIFICATE_BASE64" in texte, (
        "le nom du secret attendu n'est plus documenté dans le workflow")
    # Référencé via secrets.*, jamais écrit en clair.
    for ligne in texte.splitlines():
        if "MSIX_CERTIFICATE" in ligne and "secrets." not in ligne:
            assert ("#" in ligne or "env:" in ligne or "name:" in ligne
                    or "if:" in ligne or "$env:" in ligne or "-" == ligne.strip()[:1]), (
                f"secret potentiellement en dur : {ligne.strip()}")
    assert "BEGIN PRIVATE KEY" not in texte


def test_workflow_echoue_avant_de_publier():
    """Un build ou une validation en échec ne doit RIEN publier.

    Une release contenant un paquet qui ne s'installe pas coûte plus cher
    qu'une absence de release : elle se propage aux utilisateurs.
    """
    texte = WORKFLOW.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        # Sans PyYAML, on se contente du contrôle textuel : l'étape de
        # publication doit être conditionnée au tag.
        assert "startsWith(github.ref, 'refs/tags/')" in texte
        return

    workflow = yaml.safe_load(texte)
    job = workflow["jobs"]["build"]
    etapes = job["steps"]
    noms = [str(e.get("name", "")) for e in etapes]

    index_validation = next(
        (i for i, e in enumerate(etapes)
         if "validate-msix" in str(e.get("run", ""))), None)
    index_release = next(
        (i for i, e in enumerate(etapes)
         if "gh-release" in str(e.get("uses", ""))), None)
    assert index_validation is not None, f"aucune étape de validation ({noms})"
    assert index_release is not None, f"aucune étape de release ({noms})"
    assert index_validation < index_release, (
        "la release est créée AVANT la validation du paquet")

    # Aucune étape de publication ne doit s'exécuter malgré un échec amont.
    for etape in etapes[index_validation:]:
        condition = str(etape.get("if", ""))
        assert "always()" not in condition, (
            f"l'étape « {etape.get('name')} » publierait même après un échec")


def test_tests_msix_dans_la_suite_et_dans_la_ci():
    """Ces tests tournent réellement, au lieu de dormir dans un fichier."""
    runner = (RACINE / "python" / "tests" / "run_tests.py").read_text(encoding="utf-8")
    assert "test_msix" in runner, "test_msix.py n'est pas lancé par la suite"

    ci = (RACINE / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "packaging/**" in ci, (
        "modifier packaging/msix ne déclenche aucun test")


def run():
    test_structure_de_packaging_presente()
    test_gabarit_ne_fige_aucune_valeur_variable()
    test_manifeste_rempli_est_valide()
    test_version_msix_vient_du_fichier_version()
    test_capacites_declarees_minimales()
    test_identite_provisoire_clairement_marquee()
    test_nom_editeur_distinct_du_nom_application()
    test_identite_partner_center_renseignee()
    test_aucun_caractere_invisible_dans_lidentite()
    test_validation_controle_les_trois_champs_didentite()
    test_identite_surchargeable_sans_modifier_le_depot()
    test_icones_referencees_existent_aux_bonnes_dimensions()
    test_variantes_barre_des_taches_presentes()
    test_generateur_dassets_couvre_le_manifeste()
    test_scripts_powershell_lisibles_par_windows_powershell_51()
    test_scripts_powershell_signalent_leurs_echecs()
    test_scripts_ne_contiennent_aucun_secret()
    test_certificat_de_test_genere_jamais_versionne()
    test_sujet_du_certificat_lie_au_manifeste()
    test_commande_unique_pour_construire_le_msix()
    test_build_msix_reutilise_la_chaine_existante()
    test_application_detecte_le_conteneur_msix()
    test_terminal_portable_refuse_dans_un_paquet_en_lecture_seule()
    test_garde_msix_appliquee_dans_mt5_installer()
    test_diagnostic_expose_letat_du_paquet()
    test_donnees_utilisateur_hors_du_paquet()
    test_workflow_msix_present_et_declenche_par_un_tag()
    test_workflow_publie_paquet_et_empreintes()
    test_workflow_ne_contient_aucun_secret_en_dur()
    test_workflow_echoue_avant_de_publier()
    test_tests_msix_dans_la_suite_et_dans_la_ci()
    print("  ✓ 31 tests empaquetage MSIX / Microsoft Store")


if __name__ == "__main__":
    run()
    print("✅ MSIX OK")
