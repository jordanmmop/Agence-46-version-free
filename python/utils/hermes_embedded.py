"""Hermès intégré — second moteur d'IA locale, à côté d'Ollama.

Hermès (NousResearch) est un modèle de la famille Llama ré-entraîné pour
suivre des instructions et répondre dans un format imposé — exactement ce que
l'application demande à son IA locale (« CONFIRME ou DOUTE, puis une phrase »).
Il tourne ici sur `llama-server`, le serveur de llama.cpp, qui expose une API
compatible OpenAI sur 127.0.0.1:11435.

Trois façons d'avoir Hermès, dans cet ordre :

1. **Embarqué au build** — `runtime/hermes/` contient déjà le serveur et le
   modèle GGUF (voir tools/preparer_runtimes.py). Rien à télécharger, rien à
   installer : l'application démarre le serveur et c'est tout. C'est le mode
   normal d'un exe compilé par build_exe.bat.
2. **Installé par l'application** — si le dossier embarqué est absent (dépôt
   cloné, lancement par `python start.py`), l'app télécharge llama-server puis
   le modèle, comme elle le fait déjà pour Ollama.
3. **Fourni à la main** — les variables LLAMA_SERVER_BIN et HERMES_MODEL_PATH
   pointent vers un binaire et un GGUF existants.

Le cycle de vie (état observable, journal, arrêt propre avec l'application)
reprend celui de utils/ollama_embedded.py : les deux moteurs se pilotent de la
même façon depuis l'interface.
"""
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DOSSIER = Path(os.path.expanduser("~")) / ".agence_financiere" / "hermes"

# ── Catalogue des modèles Hermès proposés ────────────────────────────
# Quantification Q4_K_M : le meilleur compromis taille/qualité pour tourner
# sur un PC sans carte graphique dédiée — le cas de la très grande majorité
# des utilisateurs de cette application.
MODELES: Dict[str, Dict[str, Any]] = {
    "hermes-3-llama-3.2-3b": {
        "libelle": "Hermes 3 — Llama 3.2 3B (rapide, ~2 Go)",
        "depot": "NousResearch/Hermes-3-Llama-3.2-3B-GGUF",
        "fichier": "Hermes-3-Llama-3.2-3B.Q4_K_M.gguf",
        "taille_go": 2.0,
    },
    "hermes-3-llama-3.1-8b": {
        "libelle": "Hermes 3 — Llama 3.1 8B (qualité, ~4,9 Go)",
        "depot": "NousResearch/Hermes-3-Llama-3.1-8B-GGUF",
        "fichier": "Hermes-3-Llama-3.1-8B.Q4_K_M.gguf",
        "taille_go": 4.9,
    },
}
MODELE_DEFAUT = "hermes-3-llama-3.2-3b"


def modele_defaut() -> str:
    """Modèle Hermès configuré (interface > variable d'environnement > défaut)."""
    nom = (os.getenv("HERMES_MODEL", "") or "").strip()
    return nom if nom in MODELES else MODELE_DEFAUT


def url_modele(cle: str) -> str:
    """URL de téléchargement du GGUF. HERMES_MODEL_URL a la priorité : elle
    permet de pointer un miroir interne ou une quantification précise sans
    toucher au code."""
    force = (os.getenv("HERMES_MODEL_URL", "") or "").strip()
    if force:
        return force
    info = MODELES.get(cle) or MODELES[MODELE_DEFAUT]
    return (f"https://huggingface.co/{info['depot']}/resolve/main/"
            f"{info['fichier']}?download=true")


def nom_fichier_modele(cle: str) -> str:
    info = MODELES.get(cle) or MODELES[MODELE_DEFAUT]
    return str(info["fichier"])


# ── llama-server (llama.cpp) ─────────────────────────────────────────
# Les archives de llama.cpp portent le numéro de build dans leur nom
# (llama-b6421-bin-win-cpu-x64.zip) : aucune URL « latest/download/<nom> »
# n'est donc possible. On interroge l'API des releases et on retient le
# premier actif dont le nom correspond à la plateforme, du plus spécifique au
# plus générique.
_DEPOT_LLAMA = "ggml-org/llama.cpp"
_MOTIFS_ASSET: Dict[tuple, List[str]] = {
    ("Windows", "amd64"): [r"bin-win-cpu-x64\.zip$", r"bin-win-avx2-x64\.zip$",
                           r"bin-win-avx-x64\.zip$", r"bin-win-.*x64\.zip$"],
    ("Windows", "arm64"): [r"bin-win-cpu-arm64\.zip$", r"bin-win-.*arm64\.zip$"],
    ("Linux",   "amd64"): [r"bin-ubuntu-x64\.zip$", r"bin-linux-.*x64\.zip$"],
    ("Linux",   "arm64"): [r"bin-ubuntu-arm64\.zip$", r"bin-linux-.*arm64\.zip$"],
    ("Darwin",  "arm64"): [r"bin-macos-arm64\.zip$"],
    ("Darwin",  "amd64"): [r"bin-macos-x64\.zip$"],
}


def _architecture() -> str:
    m = (platform.machine() or "").lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64", "x64"):
        return "amd64"
    return m or "amd64"


def _opener():
    """Opener sans proxy système : les appels visent localhost ou des CDN
    publics, un proxy d'entreprise mal configuré ne doit pas les casser."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ouvrir_distant(requete, timeout: float):
    """Ouverture d'une URL DISTANTE, avec repli sur plusieurs magasins de
    certificats (antivirus inspectant le HTTPS, magasin Windows incomplet).
    Les appels vers 127.0.0.1 gardent `_opener`, plus léger."""
    from utils import reseau
    return reseau.ouvrir(requete, timeout=timeout)


def _nom_binaire() -> str:
    return "llama-server.exe" if platform.system() == "Windows" else "llama-server"


def urls_llama_server() -> List[str]:
    """URLs candidates du serveur llama.cpp pour cette plateforme.

    LLAMA_SERVER_URL court-circuite la découverte (miroir interne, version
    figée). Sinon on lit les releases GitHub ; en cas d'échec réseau la liste
    revient vide et l'appelant affiche une erreur explicite plutôt que de
    tenter une URL inventée.
    """
    force = (os.getenv("LLAMA_SERVER_URL", "") or "").strip()
    if force:
        return [force]

    motifs = _MOTIFS_ASSET.get((platform.system(), _architecture()))
    if not motifs:
        return []

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_DEPOT_LLAMA}/releases/latest",
            headers={"User-Agent": "AgenceNumerique",
                     "Accept": "application/vnd.github+json"},
        )
        with _ouvrir_distant(req, 30) as resp:
            release = json.loads(resp.read())
    except Exception as e:
        logger.warning(f"[Hermès] Releases llama.cpp injoignables : {e}")
        return []

    actifs = release.get("assets") or []
    urls: List[str] = []
    for motif in motifs:                     # du plus spécifique au plus large
        for actif in actifs:
            nom = str(actif.get("name") or "")
            url = str(actif.get("browser_download_url") or "")
            if url and re.search(motif, nom) and url not in urls:
                urls.append(url)
    return urls


class HermesEmbedded:
    """Cycle de vie complet du moteur Hermès (llama.cpp + modèle GGUF)."""

    def __init__(self):
        self.dossier = DOSSIER
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._atexit_enregistre = False
        self.etape = "inactif"      # inactif|telechargement|extraction|modele|demarrage|pret|erreur
        self.progression = 0.0
        self.detail = ""
        self.erreur = ""
        self._erreur_demarrage = ""

    # ── Localisation des ressources ───────────────────────────────────
    @staticmethod
    def binaire_embarque() -> Optional[Path]:
        """llama-server livré DANS l'application (aucune installation)."""
        from utils import runtime_embarque
        return runtime_embarque.premier("llama-server*", "hermes", "bin")

    def binaire_local(self) -> Optional[Path]:
        """llama-server téléchargé par l'application dans son dossier."""
        cible = self.dossier / "bin" / _nom_binaire()
        try:
            return cible if cible.is_file() else None
        except Exception:
            return None

    def binaire(self) -> Optional[Path]:
        """Serveur réellement lançable, quelle que soit son origine."""
        force = (os.getenv("LLAMA_SERVER_BIN", "") or "").strip()
        if force and Path(force).is_file():
            return Path(force)
        return self.binaire_embarque() or self.binaire_local() or self._binaire_systeme()

    @staticmethod
    def _binaire_systeme() -> Optional[Path]:
        """llama-server déjà installé sur la machine (brew, apt, build local)."""
        trouve = shutil.which("llama-server")
        return Path(trouve) if trouve else None

    @staticmethod
    def modele_embarque(cle: str = "") -> Optional[Path]:
        """GGUF livré DANS l'application. Le modèle demandé est privilégié ;
        à défaut n'importe quel GGUF embarqué fait l'affaire — mieux vaut le
        modèle présent que pas d'IA du tout."""
        from utils import runtime_embarque
        if cle:
            exact = runtime_embarque.premier(nom_fichier_modele(cle), "hermes", "models")
            if exact:
                return exact
        return runtime_embarque.premier("*.gguf", "hermes", "models")

    def modele_local(self, cle: str = "") -> Optional[Path]:
        """GGUF téléchargé par l'application."""
        dossier = self.dossier / "models"
        if not dossier.is_dir():
            return None
        if cle:
            exact = dossier / nom_fichier_modele(cle)
            if exact.is_file():
                return exact
        try:
            for p in sorted(dossier.glob("*.gguf")):
                if p.is_file():
                    return p
        except Exception:
            pass
        return None

    def modele(self, cle: str = "") -> Optional[Path]:
        """Fichier de modèle réellement utilisable."""
        cle = cle or modele_defaut()
        force = (os.getenv("HERMES_MODEL_PATH", "") or "").strip()
        if force and Path(force).is_file():
            return Path(force)
        return self.modele_embarque(cle) or self.modele_local(cle)

    # ── État ──────────────────────────────────────────────────────────
    def est_installe(self) -> bool:
        """Serveur ET modèle présents : Hermès est utilisable hors ligne."""
        return self.binaire() is not None and self.modele() is not None

    def est_disponible(self) -> bool:
        return self.est_installe()

    @staticmethod
    def serveur_repond(timeout: float = 2.0) -> bool:
        """Un llama-server répond-il sur le port d'Hermès ?

        Comme pour Ollama, une réponse HTTP d'erreur prouve qu'un serveur est
        bien là : c'est ce qui compte pour décider d'un démarrage.
        """
        from config import HERMES_URL
        try:
            with _opener().open(f"{HERMES_URL}/v1/models", timeout=timeout):
                return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    def get_status(self) -> Dict[str, Any]:
        cle = modele_defaut()
        binaire = self.binaire()
        modele = self.modele(cle)
        embarque = (self.binaire_embarque() is not None
                    and self.modele_embarque(cle) is not None)
        return {
            "installe":      self.est_installe(),
            "disponible":    self.est_disponible(),
            "embarque":      embarque,
            "serveur_actif": self.serveur_repond(),
            "processus_gere": self._proc is not None and self._proc.poll() is None,
            "binaire":       str(binaire) if binaire else "",
            "modele":        str(modele) if modele else "",
            "modele_cle":    cle,
            "modeles":       [{"cle": k, "libelle": v["libelle"],
                               "taille_go": v["taille_go"]}
                              for k, v in MODELES.items()],
            "etape":         self.etape,
            "progression":   round(self.progression, 1),
            "detail":        self.detail,
            "erreur":        self.erreur,
            "dossier":       str(self.dossier),
            "journal":       self._queue_log(200),
        }

    # ── Installation (uniquement si rien n'est embarqué) ──────────────
    def installer_async(self, modele: str = None, force: bool = False) -> Dict[str, Any]:
        cle = modele if modele in MODELES else modele_defaut()
        with self._lock:
            if self.etape in ("telechargement", "extraction", "modele", "demarrage"):
                return {"success": False, "error": "Installation déjà en cours"}
            self.etape = "telechargement"
            self.progression = 0.0
            self.erreur = ""
        if force:
            if not self._arreter_serveur():
                with self._lock:
                    self.etape = "erreur"
                    self.erreur = ("Un serveur Hermès est encore actif et verrouille "
                                   "les fichiers. Fermez-le puis relancez.")
                return {"success": False, "error": self.erreur}
            try:
                shutil.rmtree(self.dossier)
            except FileNotFoundError:
                pass
            except OSError as e:
                with self._lock:
                    self.etape = "erreur"
                    self.erreur = (f"Purge du dossier Hermès impossible ({e}). "
                                   "Fermez le serveur (ou l'antivirus) et réessayez.")
                return {"success": False, "error": self.erreur}
        threading.Thread(target=self._installer, args=(cle,), daemon=True).start()
        return {"success": True}

    def _installer(self, cle: str):
        try:
            if self.binaire() is None:
                self._telecharger_serveur()
            if self.modele(cle) is None:
                self._telecharger_modele(cle)
            self.etape = "demarrage"
            self.detail = ("Démarrage du moteur Hermès... (le premier démarrage "
                           "charge le modèle en mémoire, 30 s à 2 min)")
            if not self.demarrer(attente_max=180):
                raise RuntimeError(self._erreur_demarrage
                                   or "Le serveur Hermès n'a pas démarré")
            self.etape = "pret"
            self.progression = 100.0
            self.detail = f"Hermès prêt — modèle {cle}"
        except Exception as e:
            logger.error(f"[Hermès] Installation : {e}")
            self.etape = "erreur"
            self.erreur = str(e)

    def _telecharger_serveur(self):
        self.etape = "telechargement"
        self.progression = 0.0
        self.detail = "Téléchargement du moteur Hermès (llama.cpp, ~50 Mo)..."
        urls = urls_llama_server()
        if not urls:
            raise RuntimeError(
                f"Aucun moteur llama.cpp disponible pour {platform.system()}/"
                f"{_architecture()}. Compilez le vôtre et indiquez-le avec la "
                "variable LLAMA_SERVER_BIN, ou choisissez le moteur Ollama.")
        (self.dossier / "bin").mkdir(parents=True, exist_ok=True)
        archive = self.dossier / "llama.zip"
        derniere = None
        for url in urls:
            try:
                logger.info(f"[Hermès] Téléchargement {url}")
                self._telecharger_fichier(url, archive)
                self._extraire_serveur(archive)
                return
            except Exception as e:
                derniere = e
                archive.unlink(missing_ok=True)
        raise RuntimeError(
            f"Téléchargement du moteur impossible ({derniere}). Réessayez, ou "
            "choisissez le moteur Ollama dans la configuration.")

    def _extraire_serveur(self, archive: Path):
        self.etape = "extraction"
        self.progression = 0.0
        self.detail = "Extraction du moteur..."
        cible = self.dossier / "bin"
        nom = _nom_binaire()
        with zipfile.ZipFile(archive) as z:
            membres = z.namelist()
            # L'archive contient le serveur ET ses bibliothèques partagées
            # (ggml, llama). Extraire le seul exécutable donnerait un binaire
            # qui refuse de démarrer : on prend tout le dossier qui le porte.
            #
            # PurePosixPath, et non Path : un fichier ZIP nomme TOUJOURS ses
            # membres avec des « / », alors que Path() les traduit en « \ »
            # sous Windows. Le préfixe calculé n'aurait alors correspondu à
            # aucun membre, l'extraction se serait terminée sans copier un
            # seul fichier — précisément sur la plateforme visée.
            porteurs = [m for m in membres if PurePosixPath(m).name == nom]
            if not porteurs:
                raise RuntimeError(f"{nom} absent de l'archive llama.cpp")
            prefixe = str(PurePosixPath(porteurs[0]).parent)
            for membre in membres:
                if membre.endswith("/"):
                    continue
                if prefixe not in (".", "") and not membre.startswith(prefixe):
                    continue
                # Chemin APLATI dans bin/ : les archives varient
                # (build/bin/…, llama-bXXXX/…) et le serveur cherche ses
                # bibliothèques à côté de lui.
                destination = cible / PurePosixPath(membre).name
                with z.open(membre) as src, open(destination, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        archive.unlink(missing_ok=True)
        if platform.system() != "Windows":
            for p in cible.iterdir():
                if p.is_file() and (p.name == nom or p.suffix in ("", ".so")):
                    try:
                        p.chmod(0o755)
                    except Exception:
                        pass
        if self.binaire_local() is None:
            raise RuntimeError("Moteur extrait mais introuvable — archive inattendue")

    def _telecharger_modele(self, cle: str):
        self.etape = "modele"
        self.progression = 0.0
        info = MODELES.get(cle) or MODELES[MODELE_DEFAUT]
        self.detail = f"Téléchargement du modèle {cle} (~{info['taille_go']} Go)..."
        dossier = self.dossier / "models"
        dossier.mkdir(parents=True, exist_ok=True)
        cible = dossier / nom_fichier_modele(cle)
        try:
            self._telecharger_fichier(url_modele(cle), cible, timeout=120)
        except Exception as e:
            cible.unlink(missing_ok=True)
            raise RuntimeError(
                f"Téléchargement du modèle Hermès impossible ({e}). Vérifiez "
                "votre connexion, ou choisissez le moteur Ollama.")

    def _telecharger_fichier(self, url: str, cible: Path, timeout: float = 60):
        """Téléchargement avec progression et contrôle de complétude.

        Une coupure « propre » (proxy qui ferme la connexion) sort de la
        boucle sans exception : sans le contrôle final, un GGUF TRONQUÉ de
        plusieurs gigaoctets serait conservé et le serveur échouerait plus
        tard sur un message incompréhensible.
        """
        cible.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "AgenceNumerique"})
        with _ouvrir_distant(req, timeout) as resp, open(cible, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            lu = 0
            while True:
                bloc = resp.read(1024 * 512)
                if not bloc:
                    break
                f.write(bloc)
                lu += len(bloc)
                if total:
                    self.progression = lu / total * 100
        if total and lu < total:
            cible.unlink(missing_ok=True)
            raise RuntimeError(f"téléchargement incomplet ({lu}/{total} octets)")

    # ── Serveur ───────────────────────────────────────────────────────
    def demarrer(self, attente_max: float = 120.0) -> bool:
        """Démarre `llama-server` en arrière-plan si nécessaire."""
        self._erreur_demarrage = ""
        if self.serveur_repond():
            return True

        exe = self.binaire()
        if exe is None:
            self._erreur_demarrage = ("Moteur Hermès introuvable (ni embarqué, ni "
                                      "installé).")
            return False
        modele = self.modele()
        if modele is None:
            self._erreur_demarrage = "Modèle Hermès introuvable."
            return False

        from config import HERMES_URL
        port = HERMES_URL.rsplit(":", 1)[-1] or "11435"

        self.dossier.mkdir(parents=True, exist_ok=True)
        self._log_path = self.dossier / "serve.log"
        log_f = open(self._log_path, "ab")

        # -c 4096 : les invites de l'application tiennent largement dedans, et
        # une fenêtre plus grande multiplierait la mémoire consommée sur des
        # machines qui font déjà tourner MetaTrader et le tableau de bord.
        commande = [str(exe), "-m", str(modele),
                    "--host", "127.0.0.1", "--port", str(port),
                    "-c", "4096", "--no-warmup"]
        kwargs: Dict[str, Any] = {"stdout": log_f, "stderr": subprocess.STDOUT}
        if platform.system() == "Windows":
            kwargs["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
        # Les bibliothèques du serveur sont à CÔTÉ de lui (extraction aplatie
        # ci-dessus) : sans ce répertoire de travail, Windows ne les trouve pas
        # et le processus meurt aussitôt, sans message.
        kwargs["cwd"] = str(Path(exe).parent)

        logger.info(f"[Hermès] Démarrage : {exe} -m {modele}")
        try:
            self._proc = subprocess.Popen(commande, **kwargs)
        except Exception as e:
            self._erreur_demarrage = f"Lancement impossible : {e}"
            return False
        finally:
            log_f.close()

        if not self._atexit_enregistre:
            import atexit
            atexit.register(self.arreter)
            self._atexit_enregistre = True

        fin = time.time() + attente_max
        while time.time() < fin:
            if self.serveur_repond():
                return True
            if self._proc.poll() is not None:
                self._erreur_demarrage = (
                    f"Le serveur s'est arrêté (code {self._proc.returncode}). "
                    f"Détail : {self._queue_log()}")
                return False
            time.sleep(0.8)
        self._erreur_demarrage = (f"Pas de réponse après {int(attente_max)}s. "
                                  f"Journal : {self._queue_log()}")
        return False

    def demarrer_si_possible(self):
        """Appelé au boot de l'application quand Hermès est le moteur choisi."""
        try:
            if self.serveur_repond():
                return
            if self.est_installe():
                self.demarrer(attente_max=90)
        except Exception as e:
            logger.warning(f"[Hermès] Démarrage auto : {e}")

    def arreter(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _arreter_serveur(self) -> bool:
        """Arrête le serveur et attend la libération du port."""
        self.arreter()
        if not self.serveur_repond():
            return True
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                               capture_output=True, timeout=10)
            else:
                subprocess.run(["pkill", "-f", "llama-server"],
                               capture_output=True, timeout=10)
        except Exception:
            pass
        for _ in range(10):
            if not self.serveur_repond():
                return True
            time.sleep(1)
        return not self.serveur_repond()

    def _queue_log(self, max_chars: int = 300) -> str:
        try:
            data = (self.dossier / "serve.log").read_bytes()
            texte = data[-2000:].decode("utf-8", errors="replace")
            lignes = [l for l in texte.splitlines() if l.strip()]
            return " | ".join(lignes[-3:])[-max_chars:] or "(journal vide)"
        except Exception:
            return "(journal indisponible)"


# ── Singleton ─────────────────────────────────────────────────────────
_embedded: Optional[HermesEmbedded] = None
_embedded_lock = threading.Lock()


def get_hermes_embedded() -> HermesEmbedded:
    global _embedded
    if _embedded is None:
        with _embedded_lock:
            if _embedded is None:
                _embedded = HermesEmbedded()
    return _embedded
