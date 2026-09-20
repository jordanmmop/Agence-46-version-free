"""Tests du moteur IA sélectionnable (Ollama / Hermès) et des runtimes
embarqués dans l'application (Ollama, Hermès, MetaTrader 5).

L'application est 100 % locale : plusieurs de ces tests vérifient précisément
qu'aucun chemin ne peut ramener un mode distant ou une clé API.

Aucun de ces tests ne télécharge quoi que ce soit ni ne démarre un serveur :
on vérifie le ROUTAGE et la DÉTECTION, qui sont exactement les deux endroits
où un second moteur pouvait être ignoré en silence.
"""
import _setup  # noqa: F401
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def _isoler_config():
    """Config applicative dans un fichier temporaire (jamais celle de l'utilisateur)."""
    from utils import app_config
    fd, chemin = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(chemin)
    app_config._PATH = Path(chemin)
    app_config._cache = None


def _env_propre():
    """Repart d'un environnement sans choix de moteur."""
    for cle in ("MOTEUR_IA", "USE_OLLAMA", "ANTHROPIC_API_KEY",
                "OLLAMA_MODEL", "HERMES_MODEL", "AGENCE_RUNTIME_DIR"):
        os.environ.pop(cle, None)


# ── Sélection du moteur ───────────────────────────────────────────────
def test_deux_moteurs_locaux_selectionnables():
    _env_propre()
    _isoler_config()
    from utils import moteur_ia
    import config

    assert moteur_ia.definir("hermes")["success"]
    assert config.moteur_ia_actif() == "hermes"
    assert moteur_ia.definir("ollama")["success"]
    assert config.moteur_ia_actif() == "ollama"

    assert not moteur_ia.definir("inconnu")["success"]
    _env_propre()


def test_aucun_mode_distant():
    """« cloud » ne doit plus exister nulle part, même avec une clé présente.

    Une clé laissée dans l'environnement par une version précédente ne doit
    RIEN réactiver : c'est la garantie que l'application reste entièrement
    locale, y compris sur un poste déjà configuré autrement.
    """
    _env_propre()
    _isoler_config()
    import config
    from utils import moteur_ia

    assert "cloud" not in moteur_ia.MOTEURS
    refus = moteur_ia.definir("cloud")
    assert not refus["success"]

    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-cle-residuelle"
    os.environ["MOTEUR_IA"] = "cloud"
    # Une valeur périmée retombe sur un moteur qui EXISTE, sinon plus aucune
    # consultation n'aboutirait.
    assert config.moteur_ia_actif() == "ollama"
    assert config.use_ollama_actif() is True
    assert not hasattr(config, "get_api_key")
    _env_propre()


def test_cle_api_effacee_du_disque():
    """Une clé enregistrée par une version précédente est EFFACÉE.

    L'application ne s'en sert plus : la laisser dormir dans config.json
    serait un secret conservé sans raison.
    """
    _env_propre()
    from utils import app_config
    fd, chemin = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    Path(chemin).write_text(json.dumps({
        "ANTHROPIC_API_KEY": "sk-ant-a-effacer",
        "MOTEUR_IA": "cloud",
        "code_acces": "conserve",
    }))
    app_config._PATH = Path(chemin)
    app_config._cache = None

    cfg = app_config.tout()
    assert "ANTHROPIC_API_KEY" not in cfg
    assert cfg["MOTEUR_IA"] == "ollama"        # moteur périmé remplacé
    assert cfg["code_acces"] == "conserve"     # le reste est préservé
    # …et l'effacement est PERSISTÉ, pas seulement en mémoire
    sur_disque = json.loads(Path(chemin).read_text())
    assert "ANTHROPIC_API_KEY" not in sur_disque
    Path(chemin).unlink(missing_ok=True)
    _env_propre()


def test_hermes_compte_comme_ia_locale():
    """`use_ollama_actif()` — « l'IA est-elle locale ? » — est toujours vrai."""
    _env_propre()
    _isoler_config()
    import config
    from utils import moteur_ia

    moteur_ia.definir("hermes")
    assert config.use_ollama_actif() is True
    assert config.moteur_ia_actif() == "hermes"
    _env_propre()


def test_choix_memorise_dans_le_fichier_de_config():
    """Le moteur choisi doit survivre au redémarrage de l'application."""
    _env_propre()
    _isoler_config()
    from utils import app_config, moteur_ia

    moteur_ia.definir("hermes")
    assert app_config.get("MOTEUR_IA") == "hermes"
    # USE_OLLAMA écrit en parallèle : les écrans historiques le lisent encore.
    assert app_config.get("USE_OLLAMA") == "true"
    _env_propre()


def test_compat_ancienne_configuration():
    """Une configuration antérieure reste sur un moteur local valable."""
    _env_propre()
    _isoler_config()
    import config

    os.environ["USE_OLLAMA"] = "true"
    assert config.moteur_ia_actif() == "ollama"
    # « false » signifiait « mode cloud » : il ne doit plus rien désigner.
    os.environ["USE_OLLAMA"] = "false"
    assert config.moteur_ia_actif() == "ollama"
    _env_propre()


# ── Routage des consultations ─────────────────────────────────────────
def test_chat_route_vers_le_bon_moteur():
    """Chaque moteur doit recevoir SA requête, à SON URL, dans SON format."""
    _env_propre()
    _isoler_config()
    from utils import moteur_ia

    appels = []

    def faux_post(url, charge, timeout):
        appels.append((url, charge))
        if "/v1/chat/completions" in url:      # format OpenAI (Hermès)
            return {"choices": [{"message": {"content": "réponse hermès"}}]}
        return {"message": {"content": "réponse ollama"}}   # format Ollama

    original = moteur_ia._post_json
    moteur_ia._post_json = faux_post
    try:
        messages = [{"role": "user", "content": "test"}]

        moteur_ia.definir("ollama")
        assert moteur_ia.chat(messages) == "réponse ollama"
        assert appels[-1][0].endswith("/api/chat")

        moteur_ia.definir("hermes")
        assert moteur_ia.chat(messages) == "réponse hermès"
        assert appels[-1][0].endswith("/v1/chat/completions")
        # Sans plafond de longueur, Hermès peut dérouler jusqu'à dépasser le
        # délai des agents et faire retomber le cycle sur le texte de secours.
        assert appels[-1][1]["max_tokens"] > 0
    finally:
        moteur_ia._post_json = original
        _env_propre()


def test_moteur_injoignable_ne_leve_jamais():
    """Une IA absente rend None — jamais une exception.

    Les 46 assistants et le Chef d'Orchestre appellent `chat()` au cœur d'un
    cycle de trading : une exception y interromprait l'analyse des symboles
    suivants.
    """
    _env_propre()
    _isoler_config()
    from utils import moteur_ia

    def poste_qui_echoue(url, charge, timeout):
        raise OSError("connexion refusée")

    original = moteur_ia._post_json
    moteur_ia._post_json = poste_qui_echoue
    try:
        for nom in ("ollama", "hermes"):
            moteur_ia.definir(nom)
            assert moteur_ia.chat([{"role": "user", "content": "x"}]) is None
    finally:
        moteur_ia._post_json = original
        _env_propre()


def test_assistant_et_orchestrateur_passent_par_le_routeur():
    """Les deux appelants historiques doivent utiliser le moteur SÉLECTIONNÉ.

    Ils construisaient chacun une requête Ollama en dur : avec Hermès actif,
    l'assistant n'obtenait aucun avis et l'orchestrateur retombait sur sa
    synthèse textuelle, sans que rien ne le signale.
    """
    _env_propre()
    _isoler_config()
    from utils import moteur_ia
    from agents.assistant import AssistantIA
    from agents.orchestrateur import ChefOrchestre
    from models.signal import Signal, ActionSignal

    moteur_ia.definir("hermes")
    vus = []

    def faux_chat(messages, timeout=30.0, moteur="", temperature=0.2):
        vus.append(moteur or moteur_ia.moteur_actif())
        return "CONFIRME — cohérent avec la tendance."

    original = moteur_ia.chat
    moteur_ia.chat = faux_chat
    try:
        signal = Signal(
            agent_id="TEST-1", agent_nom="Agent test", symbole="BTC-USD",
            action=ActionSignal.ACHAT, confiance=90.0, prix_entree=100.0,
            stop_loss=98.0, take_profit=106.0, raisonnement="tendance haussière",
        )
        assistant = AssistantIA("TEST-1", "Agent test", "strategies")
        avis = assistant._avis_ia(signal)
        assert avis and avis["verdict"] == "CONFIRME"

        texte = ChefOrchestre._synthese_locale(
            ChefOrchestre.__new__(ChefOrchestre), "BTC-USD", [signal], "ACHAT", 100.0)
        assert "CONFIRME" in texte

        assert vus == ["hermes", "hermes"], f"moteurs consultés : {vus}"
    finally:
        moteur_ia.chat = original
        _env_propre()


def test_orchestrateur_voit_hermes_disponible():
    """`_moteur_local_disponible` ne doit plus tester Ollama uniquement."""
    _env_propre()
    _isoler_config()
    from utils import moteur_ia
    from agents.orchestrateur import ChefOrchestre

    moteur_ia.definir("hermes")
    original = moteur_ia.disponible
    moteur_ia.disponible = lambda nom="": nom == "hermes"
    try:
        assert ChefOrchestre._moteur_local_disponible() is True
        moteur_ia.definir("ollama")
        assert ChefOrchestre._moteur_local_disponible() is False
    finally:
        moteur_ia.disponible = original
        _env_propre()


# ── Runtimes embarqués ────────────────────────────────────────────────
def _faux_runtime(tmp: Path, avec_modeles=True) -> Path:
    """Fabrique une arborescence runtime/ comme celle produite au build."""
    import platform
    (tmp / "ollama").mkdir(parents=True, exist_ok=True)
    nom_ollama = "ollama.exe" if platform.system() == "Windows" else "bin/ollama"
    cible = tmp / "ollama" / nom_ollama
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_bytes(b"0" * 1024)
    (tmp / "hermes" / "bin").mkdir(parents=True, exist_ok=True)
    nom_serveur = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    (tmp / "hermes" / "bin" / nom_serveur).write_bytes(b"0" * 1024)
    (tmp / "mt5").mkdir(parents=True, exist_ok=True)
    (tmp / "mt5" / "mt5setup.exe").write_bytes(b"0" * 1024)
    if avec_modeles:
        (tmp / "ollama" / "models").mkdir(parents=True, exist_ok=True)
        (tmp / "hermes" / "models").mkdir(parents=True, exist_ok=True)
        (tmp / "hermes" / "models" / "Hermes-3-Llama-3.2-3B.Q4_K_M.gguf").write_bytes(b"0" * 64)
    (tmp / "manifest.json").write_text(json.dumps({"genere_le": "2026-01-01"}))
    return tmp


def test_runtime_embarque_detecte():
    """Ce qui est livré dans l'exe doit être vu comme tel."""
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        os.environ["AGENCE_RUNTIME_DIR"] = str(_faux_runtime(Path(d)))
        from utils import runtime_embarque
        etat = runtime_embarque.resume()
        assert etat["actif"] and etat["ollama"] and etat["hermes"]
        assert etat["hermes_modele"] and etat["mt5"]
        assert etat["manifeste"].get("genere_le") == "2026-01-01"
    _env_propre()


def test_moteurs_utilisent_les_binaires_embarques():
    """Avec un runtime embarqué, aucun moteur ne doit se croire « absent ».

    C'est le cœur de la promesse « déjà installé » : sans cette détection,
    l'application proposait un téléchargement de plusieurs gigaoctets alors
    qu'elle transportait déjà tout.
    """
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        os.environ["AGENCE_RUNTIME_DIR"] = str(_faux_runtime(Path(d)))
        from utils.ollama_embedded import OllamaEmbedded
        from utils.hermes_embedded import HermesEmbedded
        from utils.mt5_installer import MT5Installer

        ollama = OllamaEmbedded()
        assert ollama.ollama_livre_avec_lapp() is not None
        assert ollama.binaire() is not None
        assert ollama.get_status()["embarque"] is True

        hermes = HermesEmbedded()
        assert hermes.binaire() is not None
        assert hermes.modele() is not None
        assert hermes.est_installe() is True
        assert hermes.get_status()["embarque"] is True

        assert MT5Installer.setup_embarque() is not None
        assert MT5Installer().get_status()["embarque"] is True
    _env_propre()


def test_sans_runtime_embarque_rien_nest_casse():
    """Dépôt cloné sans runtime/ : on retombe sur l'installation à la demande."""
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        vide = Path(d) / "absent"
        os.environ["AGENCE_RUNTIME_DIR"] = str(vide)
        from utils import runtime_embarque
        from utils.hermes_embedded import HermesEmbedded
        from utils.mt5_installer import MT5Installer

        assert runtime_embarque.racine() is None
        assert runtime_embarque.chemin("hermes") is None
        assert HermesEmbedded.binaire_embarque() is None
        assert MT5Installer.setup_embarque() is None
        # Le statut doit rester interrogeable (l'interface l'affiche au boot)
        assert HermesEmbedded().get_status()["installe"] is False
    _env_propre()


def test_terminal_mt5_portable_prioritaire():
    """Un terminal portable embarqué doit passer AVANT toute installation."""
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        runtime = _faux_runtime(Path(d))
        (runtime / "mt5" / "terminal").mkdir(parents=True, exist_ok=True)
        (runtime / "mt5" / "terminal" / "terminal64.exe").write_bytes(b"0" * 64)
        os.environ["AGENCE_RUNTIME_DIR"] = str(runtime)

        from utils.mt5_installer import MT5Installer
        from utils.mt5_manager import MT5Manager

        portable = MT5Installer.terminal_embarque()
        assert portable and portable.endswith("terminal64.exe")
        assert MT5Installer.terminal_installe() == portable
        assert MT5Installer().get_status()["portable"] is True
        # Le gestionnaire doit le proposer EN PREMIER : sinon `initialize()`
        # partirait sur un autre terminal (ou en lancerait un second).
        assert MT5Manager().find_paths()[0] == portable
    _env_propre()


def test_demarrage_sans_dossier_de_travail():
    """Un moteur EMBARQUÉ démarre alors que rien n'a jamais été téléchargé.

    Le dossier de travail (~/.agence_financiere/…) n'existe alors pas, et
    l'ouverture du journal du serveur y échouait avec FileNotFoundError —
    avant même que le serveur soit lancé. Le moteur embarqué, celui qui n'a
    par définition jamais rien téléchargé, était donc le seul à ne pas
    démarrer.
    """
    _env_propre()
    import platform
    if platform.system() == "Windows":
        return                              # binaire factice non exécutable
    with tempfile.TemporaryDirectory() as d:
        runtime = Path(d) / "runtime"
        (runtime / "ollama" / "bin").mkdir(parents=True)
        faux = runtime / "ollama" / "bin" / "ollama"
        faux.write_text("#!/bin/sh\nexit 3\n")     # s'arrête aussitôt
        faux.chmod(0o755)
        os.environ["AGENCE_RUNTIME_DIR"] = str(runtime)

        from utils.ollama_embedded import OllamaEmbedded
        from utils.hermes_embedded import HermesEmbedded
        ollama = OllamaEmbedded()
        ollama.dossier = Path(d) / "jamais-cree"
        assert not ollama.dossier.exists()
        # Échec ATTENDU (le faux binaire meurt) — mais un échec RAPPORTÉ,
        # pas une exception qui remonte jusqu'au démarrage de l'application.
        assert ollama.demarrer(attente_max=3) is False
        assert ollama._erreur_demarrage

        hermes = HermesEmbedded()
        hermes.dossier = Path(d) / "jamais-cree-hermes"
        assert hermes.demarrer(attente_max=3) is False
    _env_propre()


def test_terminal_portable_lance_en_mode_portable():
    """Le terminal EMBARQUÉ doit être lancé par NOUS, avec « /portable ».

    Sans cela, la connexion reposait sur `mt5.initialize(path=…)`, qui lance
    le terminal SANS cette option : un terminal livré avec l'application se
    serait ouvert en mode normal, et surtout la librairie en aurait démarré un
    SECOND à côté de celui qui tournait déjà. MetaTrader refuse la double
    instance, la communication expire, et l'utilisateur ne voit qu'un « IPC
    timeout » alors que le terminal est ouvert sous ses yeux.
    """
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        runtime = _faux_runtime(Path(d))
        (runtime / "mt5" / "terminal").mkdir(parents=True, exist_ok=True)
        exe = runtime / "mt5" / "terminal" / "terminal64.exe"
        exe.write_bytes(b"0" * 64)
        os.environ["AGENCE_RUNTIME_DIR"] = str(runtime)

        from utils.mt5_manager import MT5Manager
        gestionnaire = MT5Manager()
        lances = []

        class _FauxProcessus:
            def poll(self): return None
            def terminate(self): pass

        import subprocess as _sp
        vrai_popen = _sp.Popen

        def _popen_espion(argv, *a, **kw):
            lances.append(list(argv))
            return _FauxProcessus()

        _sp.Popen = _popen_espion

        # Aucun terminal en cours (sinon on n'en lance pas un second) et
        # aucune réponse : ce qui est vérifié ici, c'est la COMMANDE lancée,
        # pas la connexion. `terminaux_en_cours` est neutralisé car il passe
        # lui aussi par subprocess — l'espion le capterait sinon.
        gestionnaire.terminaux_en_cours = staticmethod(lambda: 0)
        gestionnaire._terminal_repond = staticmethod(lambda timeout_ms=5000: False)
        try:
            resultat = gestionnaire.demarrer_terminal_embarque(attente_max=0.1)
        finally:
            _sp.Popen = vrai_popen

        assert lances, "aucun terminal lancé"
        # Sous Linux/macOS la commande est préfixée par « wine » : on vérifie
        # le CONTENU de la ligne de commande, pas sa première case.
        assert str(exe) in lances[0], lances[0]
        assert "/portable" in lances[0], lances[0]
        # Échec ATTENDU (rien ne répond), mais expliqué et non silencieux
        assert not resultat["success"] and "n'a pas répondu" in resultat["error"]

        # Le trading automatique est pré-activé : sans lui, MetaTrader accepte
        # la connexion puis REFUSE chaque ordre (code 10027), et l'application
        # paraît fonctionner alors que rien ne part chez le courtier.
        import configparser
        ini = exe.parent / "config" / "common.ini"
        assert ini.is_file()
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(ini, encoding="utf-16le")
        assert cfg.get("Experts", "AllowLiveTrading") == "1"
        assert cfg.get("Experts", "AllowDllImport") == "0"   # pas de code tiers
    _env_propre()


def test_terminal_embarque_jamais_relance_par_la_librairie():
    """Aucune stratégie de connexion ne doit passer `path=` sur l'embarqué.

    C'est la contrepartie du test précédent : le terminal portable tourne
    déjà, il se rejoint par un `initialize()` nu. Lui passer son chemin en
    ouvrirait un second — la panne exacte qu'on vient de corriger.
    """
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        runtime = _faux_runtime(Path(d))
        (runtime / "mt5" / "terminal").mkdir(parents=True, exist_ok=True)
        exe = runtime / "mt5" / "terminal" / "terminal64.exe"
        exe.write_bytes(b"0" * 64)
        os.environ["AGENCE_RUNTIME_DIR"] = str(runtime)

        from utils.mt5_manager import MT5Manager
        gestionnaire = MT5Manager()
        # Le terminal embarqué EST détecté comme installé…
        assert gestionnaire.terminal_embarque() == str(exe)
        assert str(exe) in gestionnaire.find_paths()

        # …mais le code source des stratégies l'écarte explicitement des
        # tentatives « path= ». Contrôle statique : la connexion réelle exige
        # la librairie MetaTrader5, absente hors Windows.
        import inspect
        source = inspect.getsource(MT5Manager._connect_real)
        assert "os.path.normcase(portable)" in source, (
            "le terminal embarqué n'est plus exclu des stratégies path=")
        assert "demarrer_terminal_embarque" in source, (
            "le terminal embarqué n'est plus démarré avant la connexion")
    _env_propre()


def test_reconnexion_auto_pour_le_terminal_adopte():
    """« Reprendre le compte ouvert » doit AUSSI être reconnecté au démarrage.

    Ce mode n'enregistre volontairement aucun mot de passe. Le démarrage ne
    testait que la présence d'identifiants : ces utilisateurs n'étaient donc
    jamais reconnectés, et devaient rouvrir la fenêtre de connexion à chaque
    lancement sans qu'aucun message ne l'explique.
    """
    _env_propre()
    _isoler_config()
    from utils import app_config
    from utils.mt5_manager import get_mt5_manager
    import backend.main as bm

    gestionnaire = get_mt5_manager()
    appels = []
    original = gestionnaire.reconnexion_auto
    gestionnaire.reconnexion_auto = lambda: appels.append(1) or {"success": True}
    try:
        # Aucun identifiant, aucun attachement → aucune tentative
        assert not gestionnaire.identifiants_enregistres().get("enregistres")
        bm._reconnexion_auto_si_demandee()
        assert appels == []

        # Choix « reprendre le compte ouvert » → tentative
        app_config.update(mt5_attacher=True)
        bm._reconnexion_auto_si_demandee()
        assert appels == [1], "le terminal adopté n'a pas été reconnecté"
    finally:
        gestionnaire.reconnexion_auto = original
        _env_propre()


def test_extraction_zip_independante_du_systeme():
    """L'extraction du moteur Hermès doit marcher AUSSI sous Windows.

    Un fichier ZIP nomme toujours ses membres avec des « / ». `Path()` les
    traduit en « \\ » sous Windows : le préfixe calculé pour ne garder que le
    dossier du serveur ne correspondait alors à AUCUN membre, et l'archive
    était « extraite » sans copier un seul fichier — sur la plateforme visée,
    et nulle part ailleurs. Le test reproduit une archive llama.cpp réelle
    (serveur + bibliothèques dans un sous-dossier) et vérifie que tout arrive.
    """
    _env_propre()
    import platform
    import shutil
    import zipfile
    from pathlib import PurePosixPath

    with tempfile.TemporaryDirectory() as d:
        os.environ["AGENCE_RUNTIME_DIR"] = str(Path(d) / "absent")
        base = Path(d)
        nom = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
        archive = base / "llama.zip"
        with zipfile.ZipFile(archive, "w") as z:
            # Arborescence typique des archives llama.cpp : tout est sous
            # « build/bin/ », séparateurs POSIX quelle que soit la plateforme.
            z.writestr(f"build/bin/{nom}", "serveur")
            z.writestr("build/bin/ggml.dll", "bibliothèque")
            z.writestr("build/bin/llama.dll", "bibliothèque")
            z.writestr("build/lisez-moi.txt", "hors du dossier du serveur")

        from utils.hermes_embedded import HermesEmbedded
        moteur = HermesEmbedded()
        moteur.dossier = base / "hermes"
        (moteur.dossier / "bin").mkdir(parents=True)
        shutil.copy(archive, moteur.dossier / "llama.zip")
        moteur._extraire_serveur(moteur.dossier / "llama.zip")

        extraits = sorted(p.name for p in (moteur.dossier / "bin").iterdir())
        # Le serveur ET ses bibliothèques : extraire le seul exécutable
        # donnerait un binaire qui refuse de démarrer.
        assert nom in extraits, extraits
        assert "ggml.dll" in extraits and "llama.dll" in extraits, extraits
        # Ce qui est HORS du dossier du serveur ne suit pas.
        assert "lisez-moi.txt" not in extraits, extraits
        assert moteur.binaire_local() is not None

        # Le calcul du préfixe ne doit jamais dépendre du séparateur du système
        assert str(PurePosixPath("build/bin/x").parent) == "build/bin"
    _env_propre()


def test_sonde_ne_demarre_jamais_de_terminal():
    """La sonde ne doit PAS pouvoir ouvrir un terminal MetaTrader.

    `mt5.initialize()`, même sans chemin, DÉMARRE un terminal quand aucun ne
    tourne. La sonde étant appelée depuis six endroits — dont une boucle
    d'attente rejouée toutes les deux secondes — chaque sondage en ouvrait un
    de plus : deux fenêtres, puis dix, puis autant que de sondages. Et comme
    un seul terminal peut réserver le port de communication local, tous les
    suivants étaient inutilisables (« MCP bind error […] 10048 »), d'où
    l'« IPC timeout » avec MetaTrader pourtant ouvert à l'écran.
    """
    _env_propre()
    from utils.mt5_manager import MT5Manager

    appels_initialize = []

    class _FauxMT5:
        def initialize(self, **kw):
            appels_initialize.append(kw)
            return True
        def shutdown(self): pass

    import sys as _sys
    ancien = _sys.modules.get("MetaTrader5")
    _sys.modules["MetaTrader5"] = _FauxMT5()
    compte = {"n": 0}
    vrai_compte = MT5Manager.terminaux_en_cours
    try:
        # AUCUN terminal en cours → la librairie ne doit pas être touchée
        MT5Manager.terminaux_en_cours = staticmethod(lambda: compte["n"])
        assert MT5Manager._terminal_repond() is False
        assert appels_initialize == [], (
            "la sonde a appelé initialize() sans terminal — elle peut donc en "
            "démarrer un")

        # Un terminal tourne → là, on a le droit de sonder
        compte["n"] = 1
        assert MT5Manager._terminal_repond() is True
        assert len(appels_initialize) == 1
    finally:
        MT5Manager.terminaux_en_cours = vrai_compte
        if ancien is None:
            _sys.modules.pop("MetaTrader5", None)
        else:
            _sys.modules["MetaTrader5"] = ancien
        _env_propre()


def test_jamais_deux_terminaux():
    """Un terminal déjà ouvert ne doit JAMAIS en faire ouvrir un second.

    MetaTrader n'autorise qu'une réservation du port de communication : le
    second processus démarre puis échoue, et rend la communication impossible
    pour tout le monde.
    """
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        runtime = _faux_runtime(Path(d))
        (runtime / "mt5" / "terminal").mkdir(parents=True, exist_ok=True)
        (runtime / "mt5" / "terminal" / "terminal64.exe").write_bytes(b"0" * 64)
        os.environ["AGENCE_RUNTIME_DIR"] = str(runtime)

        from utils.mt5_manager import MT5Manager
        gestionnaire = MT5Manager()
        lances = []
        import subprocess as _sp
        vrai_popen = _sp.Popen
        _sp.Popen = lambda argv, *a, **kw: lances.append(list(argv))
        try:
            # Un terminal tourne et répond → on s'en sert, aucun lancement
            gestionnaire.terminaux_en_cours = staticmethod(lambda: 1)
            gestionnaire._terminal_repond = staticmethod(lambda timeout_ms=5000: True)
            r = gestionnaire.demarrer_terminal_embarque()
            assert r["success"] and r.get("deja_actif") and lances == []

            # Plusieurs terminaux → refus explicite, toujours aucun lancement
            gestionnaire.terminaux_en_cours = staticmethod(lambda: 3)
            gestionnaire._terminal_repond = staticmethod(lambda timeout_ms=5000: False)
            r = gestionnaire.demarrer_terminal_embarque()
            assert not r["success"] and "3 terminaux" in r["error"]
            assert lances == [], "un terminal a été lancé alors que 3 tournaient"
        finally:
            _sp.Popen = vrai_popen
    _env_propre()


def test_nom_de_serveur_tolerant():
    """« Ava-Demo 1-MT5 » et « Ava - Demo 1-MT5 » désignent le même serveur.

    MetaTrader n'accepte que SON orthographe : une espace autour du tiret
    suffisait à faire échouer la connexion, avec un message parlant du mot de
    passe — donc à chercher au mauvais endroit.
    """
    _env_propre()
    from utils.mt5_manager import MT5Manager
    gestionnaire = MT5Manager()
    gestionnaire.serveurs_connus = lambda: ["Ava - Demo 1-MT5", "Ava - Real 1-MT5"]
    assert gestionnaire.resoudre_serveur("Ava-Demo 1-MT5") == "Ava - Demo 1-MT5"
    assert gestionnaire.resoudre_serveur("ava demo 1 mt5") == "Ava - Demo 1-MT5"
    assert gestionnaire.resoudre_serveur("Ava - Real 1-MT5") == "Ava - Real 1-MT5"
    # Un serveur inconnu est rendu tel quel : le terminal le découvrira peut-être
    assert gestionnaire.resoudre_serveur("Autre-MT5") == "Autre-MT5"
    _env_propre()


def test_certificats_https_cascade():
    """Un magasin de certificats défaillant ne doit pas bloquer un téléchargement.

    Symptôme corrigé : « SSL: CERTIFICATE_VERIFY_FAILED — unable to get local
    issuer certificate ». Trois causes courantes sous Windows, aucune
    imputable à l'utilisateur : un antivirus qui inspecte le HTTPS et
    remplace les certificats, un exe compilé sans magasin embarqué, un magasin
    Windows incomplet. On essaie donc chaque source de confiance à tour de
    rôle — mais SANS jamais désactiver la vérification : ces fichiers sont des
    programmes que l'utilisateur va exécuter.
    """
    import ssl
    import urllib.error
    from utils import reseau

    essais = []

    class _Opener:
        def __init__(self, nom, echoue):
            self.nom, self.echoue = nom, echoue

        def open(self, requete, timeout=None):
            essais.append(self.nom)
            if self.echoue:
                raise ssl.SSLCertVerificationError(
                    "unable to get local issuer certificate")
            return "RÉPONSE"

    original = reseau._openers
    try:
        # La 1re source échoue (autorité de l'antivirus inconnue) → repli
        reseau._openers = lambda: [("système", _Opener("système", True)),
                                   ("certifi", _Opener("certifi", False))]
        assert reseau.ouvrir("https://exemple", 5) == "RÉPONSE"
        assert essais == ["système", "certifi"], essais

        # Toutes échouent → une erreur qui NOMME la cause et le remède
        reseau._openers = lambda: [("a", _Opener("a", True)),
                                   ("b", _Opener("b", True))]
        try:
            reseau.ouvrir("https://exemple", 5)
            raise AssertionError("aucune erreur levée")
        except reseau.CertificatIntrouvable as e:
            texte = str(e)
            assert "antivirus" in texte and "certificat" in texte, texte

        # Une panne réseau ORDINAIRE ne doit pas être rejouée sur les autres
        # magasins : la confiance n'y est pour rien, et masquer la vraie cause
        # ferait chercher au mauvais endroit.
        essais.clear()

        class _Refus:
            def open(self, requete, timeout=None):
                essais.append("essai")
                raise urllib.error.URLError(ConnectionRefusedError("refusée"))

        reseau._openers = lambda: [("a", _Refus()), ("b", _Refus())]
        try:
            reseau.ouvrir("https://exemple", 5)
            raise AssertionError("aucune erreur levée")
        except urllib.error.URLError:
            pass
        assert len(essais) == 1, f"panne réseau rejouée {len(essais)} fois"
    finally:
        reseau._openers = original


def test_aucun_telechargement_sans_verification():
    """La vérification des certificats n'est JAMAIS désactivée.

    Ces téléchargements sont des programmes que l'utilisateur va exécuter :
    accepter n'importe quel certificat reviendrait à accepter n'importe quel
    programme. Contrôle statique — c'est un invariant de sécurité, pas un
    comportement à observer.
    """
    import inspect
    from utils import reseau
    source = inspect.getsource(reseau)
    for interdit in ("CERT_NONE", "check_hostname = False",
                     "check_hostname=False", "_create_unverified_context"):
        assert interdit not in source, (
            f"la vérification des certificats est désactivée quelque part "
            f"({interdit})")

    # Et aucun module de l'application ne doit le faire non plus.
    racine = Path(__file__).resolve().parent.parent
    for chemin in racine.rglob("*.py"):
        if "test" in chemin.name:
            continue
        texte = chemin.read_text(encoding="utf-8", errors="ignore")
        assert "_create_unverified_context" not in texte, chemin
        assert "CERT_NONE" not in texte, chemin


def _module_build():
    """Charge tools/preparer_runtimes.py comme un module."""
    import importlib.util
    chemin = Path(__file__).resolve().parent.parent.parent / "tools" / "preparer_runtimes.py"
    spec = importlib.util.spec_from_file_location("preparer_runtimes", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_terminal_embarque_doit_etre_avatrade():
    """Un terminal MetaTrader GÉNÉRIQUE ne doit jamais être embarqué.

    Il démarre parfaitement, mais ne connaît AUCUN serveur AvaTrade : la
    connexion au compte échoue alors sans cause visible, et l'utilisateur
    cherche du côté de son mot de passe. Livrer un tel terminal est pire que
    n'en livrer aucun — le script doit refuser, pas avertir.
    """
    module = _module_build()
    generique = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    avatrade = r"C:\Program Files\AvaTrade MT5 Terminal\terminal64.exe"

    # Le terminal du courtier est retenu quand il existe
    assert module._choisir_terminal([generique, avatrade]) == avatrade
    # Seul un générique disponible → REFUS (chaîne vide), pas un repli discret
    assert module._choisir_terminal([generique]) == ""
    # …sauf demande explicite (--terminal-mt5-generique)
    assert module._choisir_terminal([generique], exiger_avatrade=False) == generique


def test_terminal_sans_serveurs_avatrade_est_retire():
    """Le contrôle FINAL retire un terminal copié qui ne connaît pas AvaTrade.

    Le chemin d'installation ne dit pas toujours le courtier : la vérité, ce
    sont les fichiers .srv. S'ils ne contiennent aucun serveur AvaTrade, le
    dossier est retiré plutôt que livré inutilisable.
    """
    module = _module_build()
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        # Installation source : un terminal SANS serveur AvaTrade
        source = base / "source"
        source.mkdir()
        (source / "terminal64.exe").write_bytes(b"0" * 64)
        (source / "config").mkdir()
        (source / "config" / "MetaQuotes-Demo.srv").write_bytes(b"0" * 16)

        setup = base / "avatrade5setup.exe"
        setup.write_bytes(b"AvaTrade installer ava.trade" + b"0" * 1000)
        assert module._installeur_est_avatrade(setup)

        destination = base / "embarque"
        module._terminaux_installes = lambda: [str(source / "terminal64.exe")]

        class _Proc:
            def poll(self): return 0
            def terminate(self): pass
        import subprocess as _sp
        vrai = _sp.Popen
        _sp.Popen = lambda *a, **kw: _Proc()
        try:
            module._produire_terminal_portable(setup, destination)
        finally:
            _sp.Popen = vrai

        assert not destination.exists(), (
            "un terminal sans serveur AvaTrade a été embarqué")

        # Avec un serveur AvaTrade, il est conservé
        (source / "config" / "Ava-Demo 1-MT5.srv").write_bytes(b"0" * 16)
        _sp.Popen = lambda *a, **kw: _Proc()
        try:
            module._produire_terminal_portable(setup, destination)
        finally:
            _sp.Popen = vrai
        assert (destination / "terminal64.exe").is_file()
        assert any("ava" in s.lower()
                   for s in module._serveurs_du_terminal(destination))


def test_installeur_generique_ne_produit_pas_de_terminal():
    """Sans l'installeur du COURTIER, aucun terminal portable n'est produit.

    L'installeur générique reste embarqué (l'application peut au moins
    installer MetaTrader), mais il ne sert pas à fabriquer le terminal livré.
    """
    module = _module_build()
    with tempfile.TemporaryDirectory() as d:
        setup = Path(d) / "mt5setup.exe"
        setup.write_bytes(b"MetaQuotes generic installer" + b"0" * 1000)
        assert not module._installeur_est_avatrade(setup)

        destination = Path(d) / "embarque"
        lances = []
        import subprocess as _sp
        vrai = _sp.Popen
        _sp.Popen = lambda *a, **kw: lances.append(a)
        try:
            module._produire_terminal_portable(setup, destination)
        finally:
            _sp.Popen = vrai
        assert not destination.exists()
        assert lances == [], "l'installeur générique a été exécuté"


def test_courtier_embarque_expose_a_lutilisateur():
    """L'application doit savoir dire quel courtier son terminal connaît."""
    _env_propre()
    with tempfile.TemporaryDirectory() as d:
        runtime = Path(d) / "runtime"
        (runtime / "mt5" / "terminal").mkdir(parents=True)
        (runtime / "mt5" / "terminal" / "terminal64.exe").write_bytes(b"0" * 64)
        (runtime / "manifest.json").write_text(json.dumps({
            "mt5": {"courtier": "avatrade",
                    "serveurs": ["Ava-Demo 1-MT5", "Ava-Real 1-MT5"]},
        }), encoding="utf-8")
        os.environ["AGENCE_RUNTIME_DIR"] = str(runtime)

        from utils.mt5_installer import MT5Installer
        assert MT5Installer.courtier_embarque() == "avatrade"
        assert "Ava-Demo 1-MT5" in MT5Installer.serveurs_embarques()
        etat = MT5Installer().get_status()
        assert etat["courtier"] == "avatrade"
        assert etat["portable"] is True
    _env_propre()


def test_catalogue_hermes_coherent():
    """Chaque modèle du catalogue doit produire un nom de fichier et une URL."""
    from utils import hermes_embedded as he
    assert he.MODELE_DEFAUT in he.MODELES
    for cle, info in he.MODELES.items():
        assert info["fichier"].endswith(".gguf")
        assert he.nom_fichier_modele(cle) == info["fichier"]
        url = he.url_modele(cle)
        assert url.startswith("https://") and info["depot"] in url


def test_statut_expose_les_trois_moteurs():
    """L'interface doit recevoir les deux moteurs locaux en un seul appel."""
    _env_propre()
    _isoler_config()
    from utils import moteur_ia

    moteur_ia.definir("hermes")
    etat = moteur_ia.statut()
    assert etat["moteur"] == "hermes"
    cles = {m["cle"] for m in etat["moteurs"]}
    assert cles == {"ollama", "hermes"}
    assert all(m["local"] for m in etat["moteurs"])
    for m in etat["moteurs"]:
        assert "installe" in m and "serveur_actif" in m and "libelle" in m
    _env_propre()


# ── Diagnostic et robustesse des routes d'état ────────────────────────
def _client():
    """Client de test authentifié, sur une configuration isolée.

    Les tests qui suivent provoquent des pannes VOLONTAIRES : leurs traces
    sont donc attendues. On les tait pendant l'appel, sinon la sortie de la
    suite ressemble à un échec alors que tout se passe comme prévu.
    """
    from fastapi.testclient import TestClient
    import backend.main as bm
    _isoler_config()
    cl = TestClient(bm.app, raise_server_exceptions=False)
    cl.__enter__()
    cl.post("/api/login", json={"password": "497040"})
    return cl, bm


@contextmanager
def _pannes_attendues():
    """Coupe la sortie des journaux le temps d'une panne provoquée."""
    import logging
    racine = logging.getLogger()
    niveau = racine.level
    racine.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        racine.setLevel(niveau)


def test_status_ne_tombe_jamais_en_500():
    """`/api/status` est le battement de cœur : il ne doit pas rendre 500.

    Le tableau de bord peint un bandeau rouge en travers de l'écran dès que
    cette route échoue. Une seule valeur non sérialisable au fond du
    portefeuille rendait donc TOUTE l'application inutilisable — et le message
    affiché, « Erreur interne du serveur », ne nommait ni la cause ni
    l'endroit.
    """
    cl, bm = _client()
    try:
        casse = lambda: (_ for _ in ()).throw(RuntimeError("base verrouillée"))
        original = bm.get_orchestrateur
        bm.get_orchestrateur = casse
        try:
            with _pannes_attendues():
                r = cl.get("/api/status")
            assert r.status_code == 200, r.status_code
            d = r.json()
            assert d["statut"] == "degrade"
            assert d["orchestrateur"] is None
            # La cause doit être NOMMÉE, pas seulement constatée.
            assert "base verrouillée" in d["erreurs"][0]
            assert d["nb_total"] == 46          # le reste continue d'informer
        finally:
            bm.get_orchestrateur = original
        # Retour à la normale une fois la panne levée
        assert cl.get("/api/status").json()["statut"] == "actif"
    finally:
        cl.__exit__(None, None, None)


def test_un_assistant_casse_nen_masque_pas_45():
    """Un assistant qui ne sait pas se décrire ne fait pas disparaître les autres."""
    cl, bm = _client()
    try:
        from agents import TOUS_LES_AGENTS
        victime = TOUS_LES_AGENTS[0]
        original = victime.assistant.to_dict
        victime.assistant.to_dict = lambda: (_ for _ in ()).throw(ValueError("état illisible"))
        try:
            with _pannes_attendues():
                r = cl.get("/api/assistants")
            assert r.status_code == 200
            d = r.json()
            assert d["total"] == len(TOUS_LES_AGENTS) + 1
            en_erreur = [a for a in d["assistants"] if a.get("statut") == "erreur"]
            assert len(en_erreur) == 1 and "état illisible" in en_erreur[0]["erreur"]
        finally:
            victime.assistant.to_dict = original
    finally:
        cl.__exit__(None, None, None)


def test_une_erreur_500_devient_diagnosticable():
    """Une panne doit laisser une trace CONSULTABLE depuis l'application.

    L'exe Windows n'a pas de console : sans ce journal en mémoire, la trace
    d'une erreur de route n'existait nulle part, et « Erreur interne du
    serveur » était toute l'information dont disposait l'utilisateur.
    """
    cl, bm = _client()
    try:
        @bm.app.get("/api/_panne_de_test")
        def _panne():
            raise KeyError("symbole_absent")

        with _pannes_attendues():
            r = cl.get("/api/_panne_de_test")
        assert r.status_code == 500
        corps = r.json()
        reference = corps["reference"]
        # La session est authentifiée : la cause est nommée (le middleware
        # renvoie 401 aux autres AVANT d'arriver ici — rien ne fuit).
        assert "KeyError" in corps["detail"] and "symbole_absent" in corps["detail"]
        assert reference in corps["detail"]

        diag = cl.get("/api/diagnostic").json()
        assert diag["version"] and diag["python"]
        dernieres = [e for e in diag["erreurs"] if reference in e["message"]]
        assert dernieres, "l'erreur n'a pas été enregistrée"
        assert "KeyError: 'symbole_absent'" in dernieres[-1]["trace"]

        assert cl.post("/api/diagnostic/purger").json()["success"]
        assert cl.get("/api/diagnostic").json()["erreurs"] == []
    finally:
        cl.__exit__(None, None, None)


def test_diagnostic_survit_a_un_sous_systeme_casse():
    """C'est justement quand un sous-système est cassé qu'on consulte cette page."""
    cl, bm = _client()
    try:
        from utils import moteur_ia
        original = moteur_ia.statut
        moteur_ia.statut = lambda: (_ for _ in ()).throw(OSError("moteur injoignable"))
        try:
            with _pannes_attendues():
                r = cl.get("/api/diagnostic")
            assert r.status_code == 200
            d = r.json()
            assert "moteur injoignable" in d["ia"]["erreur"]
            assert d["agents"]["charges"] == 45      # les autres blocs répondent
        finally:
            moteur_ia.statut = original
    finally:
        cl.__exit__(None, None, None)


def run():
    from utils import app_config
    _path0, _cache0 = app_config._PATH, app_config._cache
    env0 = {c: os.environ.get(c) for c in
            ("MOTEUR_IA", "USE_OLLAMA", "ANTHROPIC_API_KEY", "OLLAMA_MODEL",
             "HERMES_MODEL", "AGENCE_RUNTIME_DIR")}
    print("\n=== Moteur IA local (Ollama / Hermès) ===")
    try:
        test_deux_moteurs_locaux_selectionnables()
        test_aucun_mode_distant()
        test_cle_api_effacee_du_disque()
        test_hermes_compte_comme_ia_locale()
        test_choix_memorise_dans_le_fichier_de_config()
        test_compat_ancienne_configuration()
        test_chat_route_vers_le_bon_moteur()
        test_moteur_injoignable_ne_leve_jamais()
        test_assistant_et_orchestrateur_passent_par_le_routeur()
        test_orchestrateur_voit_hermes_disponible()
        test_runtime_embarque_detecte()
        test_moteurs_utilisent_les_binaires_embarques()
        test_sans_runtime_embarque_rien_nest_casse()
        test_terminal_mt5_portable_prioritaire()
        test_terminal_portable_lance_en_mode_portable()
        test_terminal_embarque_jamais_relance_par_la_librairie()
        test_reconnexion_auto_pour_le_terminal_adopte()
        test_demarrage_sans_dossier_de_travail()
        test_extraction_zip_independante_du_systeme()
        test_sonde_ne_demarre_jamais_de_terminal()
        test_jamais_deux_terminaux()
        test_nom_de_serveur_tolerant()
        test_certificats_https_cascade()
        test_aucun_telechargement_sans_verification()
        test_terminal_embarque_doit_etre_avatrade()
        test_terminal_sans_serveurs_avatrade_est_retire()
        test_installeur_generique_ne_produit_pas_de_terminal()
        test_courtier_embarque_expose_a_lutilisateur()
        test_catalogue_hermes_coherent()
        test_statut_expose_les_trois_moteurs()
        test_status_ne_tombe_jamais_en_500()
        test_un_assistant_casse_nen_masque_pas_45()
        test_une_erreur_500_devient_diagnosticable()
        test_diagnostic_survit_a_un_sous_systeme_casse()
        print("  ✓ 34 tests moteur IA locale / runtimes / MetaTrader / réseau")
    finally:
        app_config._PATH, app_config._cache = _path0, _cache0
        for cle, valeur in env0.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur


if __name__ == "__main__":
    run()
    print("✅ Moteur IA OK")
