"""Ollama intégré — embarqué au build, démarré et géré par l'application.

Trois origines possibles, dans cet ordre de préférence :

1. **Embarqué** — `runtime/ollama/` contient le binaire ET les modèles déjà
   tirés, empaquetés dans l'exe par build_exe.bat. Rien à télécharger, rien à
   installer : l'application démarre le serveur au premier lancement, même
   sans connexion. C'est le mode normal d'un exe compilé.
2. **Installé par l'application** — dépôt cloné ou exe sans runtime embarqué :
   l'app télécharge le binaire officiel (github.com/ollama/ollama) dans
   ~/.agence_financiere/ollama/ puis tire le modèle choisi.
3. **Installé par l'utilisateur** — un Ollama classique (ollama.com, brew,
   apt) déjà présent est utilisé tel quel, avec SES modèles.
"""
import json
import logging
import os
import platform
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DOSSIER = Path(os.path.expanduser("~")) / ".agence_financiere" / "ollama"

# Plusieurs candidats par (système, architecture) : le nom des assets peut
# évoluer. Sans la variante arm64, un hôte ARM téléchargeait un binaire
# x86-64 INEXÉCUTABLE et l'échec était rapporté comme « installez Ollama
# manuellement », masquant la vraie cause.
_URLS = {
    ("Windows", "amd64"): [
        "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
    ],
    ("Windows", "arm64"): [
        "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-arm64.zip",
    ],
    ("Linux", "amd64"): [
        "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tgz",
    ],
    ("Linux", "arm64"): [
        "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-arm64.tgz",
    ],
    # Les binaires macOS sont universels (Intel + Apple Silicon)
    ("Darwin", "amd64"): [
        "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz",
    ],
    ("Darwin", "arm64"): [
        "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz",
    ],
}


def _architecture() -> str:
    """Normalise platform.machine() en « amd64 » ou « arm64 »."""
    m = (platform.machine() or "").lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64", "x64"):
        return "amd64"
    return m or "amd64"


def urls_pour_plateforme():
    """URLs candidates pour (système, architecture), [] si non supporté."""
    return _URLS.get((platform.system(), _architecture()), [])


def _opener():
    """Opener sans proxy système (les appels localhost/GitHub directs)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# Emplacements d'une installation NORMALE d'Ollama (ollama.com), hors de
# l'application. Sans cette table, un utilisateur ayant installé Ollama
# lui-même voyait « IA locale non détectée » et se voyait proposer un
# nouveau téléchargement de ~3 Go — alors que tout était déjà là.
def _emplacements_systeme() -> list:
    systeme = platform.system()
    chemins = []
    if systeme == "Windows":
        for var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "APPDATA"):
            base = os.environ.get(var)
            if base:
                chemins.append(Path(base) / "Programs" / "Ollama" / "ollama.exe")
                chemins.append(Path(base) / "Ollama" / "ollama.exe")
    elif systeme == "Darwin":
        chemins += [
            Path("/usr/local/bin/ollama"),
            Path("/opt/homebrew/bin/ollama"),
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    else:
        chemins += [
            Path("/usr/local/bin/ollama"),
            Path("/usr/bin/ollama"),
            Path("/bin/ollama"),
            Path(os.path.expanduser("~/.local/bin/ollama")),
        ]
    return chemins


class OllamaEmbedded:
    """Cycle de vie complet d'un Ollama embarqué dans l'application."""

    def __init__(self):
        self.dossier = DOSSIER
        self.exe = self.dossier / "ollama.exe"
        self._resoudre_exe()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._atexit_enregistre = False
        # État d'installation observable depuis l'API (page /setup)
        self.etape = "inactif"        # inactif|telechargement|extraction|demarrage|modele|pret|erreur
        self.progression = 0.0        # 0-100 pour l'étape en cours
        self.detail = ""
        self.erreur = ""
        self._erreur_demarrage = ""

    def _resoudre_exe(self):
        """Localise le binaire ollama sans toucher au reste de l'état."""
        if platform.system() == "Windows":
            self.exe = self.dossier / "ollama.exe"
            return
        for cand in (self.dossier / "bin" / "ollama", self.dossier / "ollama"):
            if cand.exists():
                self.exe = cand
                return
        self.exe = self.dossier / "bin" / "ollama"

    # ── État ──────────────────────────────────────────────────────────
    def est_installe(self) -> bool:
        """Le binaire EMBARQUÉ (téléchargé par l'app) est-il utilisable ?"""
        self._resoudre_exe()
        try:
            # > 5 Mo : un binaire minuscule = téléchargement corrompu ou
            # exe mis en quarantaine/tronqué par l'antivirus → considéré
            # absent, ce qui déclenche un re-téléchargement propre.
            return self.exe.exists() and self.exe.stat().st_size > 5_000_000
        except Exception:
            return False

    @staticmethod
    def ollama_systeme() -> Optional[Path]:
        """Ollama installé NORMALEMENT sur la machine (ollama.com, brew, apt).

        L'application ne regardait que SON dossier
        (~/.agence_financiere/ollama). Un utilisateur ayant installé Ollama
        lui-même — le cas le plus courant — voyait donc « IA locale non
        détectée », se voyait proposer un téléchargement de ~3 Go, et ses
        modèles déjà tirés restaient ignorés. On cherche d'abord dans le PATH,
        puis aux emplacements d'installation standard de chaque système.
        """
        import shutil
        trouve = shutil.which("ollama")
        if trouve:
            return Path(trouve)
        for chemin in _emplacements_systeme():
            try:
                if chemin.is_file():
                    return chemin
            except Exception:
                continue
        return None

    @staticmethod
    def ollama_livre_avec_lapp() -> Optional[Path]:
        """Binaire Ollama EMBARQUÉ dans l'application (runtime/ollama/).

        Rempli au build par tools/preparer_runtimes.py. Sa présence rend
        inutile tout téléchargement : c'est ce qui permet de livrer une
        application « déjà installée ».
        """
        from utils import runtime_embarque
        for parties in (("ollama", "ollama.exe"),
                        ("ollama", "bin", "ollama"),
                        ("ollama", "ollama")):
            trouve = runtime_embarque.chemin(*parties)
            if trouve and trouve.is_file():
                return trouve
        return None

    def binaire(self) -> Optional[Path]:
        """Binaire réellement lançable : l'embarqué au build en priorité, puis
        celui téléchargé par l'app, puis celui du système. None si Ollama est
        introuvable partout."""
        livre = self.ollama_livre_avec_lapp()
        if livre is not None:
            return livre
        if self.est_installe():
            return self.exe
        return self.ollama_systeme()

    def dossier_modeles(self) -> Path:
        """Dossier OLLAMA_MODELS à utiliser (modèles embarqués si présents).

        Les modèles embarqués sont livrés DANS l'application. Ollama a besoin
        d'écrire dans son dossier de modèles (fichiers de verrouillage, mise à
        jour des manifestes) : si l'emplacement embarqué n'est pas accessible
        en écriture — application installée dans Program Files, bundle
        extrait en lecture seule — on le recopie UNE FOIS dans le dossier de
        travail. Sans ce repli, le serveur démarrait puis échouait sur chaque
        requête avec une erreur de permission incompréhensible.
        """
        from utils import runtime_embarque
        propre = self.dossier / "models"
        embarques = runtime_embarque.chemin("ollama", "models")
        if embarques is None:
            return propre
        if propre.is_dir() and any(propre.iterdir()):
            return propre                      # déjà recopiés (ou déjà tirés)
        if os.access(str(embarques), os.W_OK):
            return Path(embarques)
        try:
            import shutil
            self.detail = "Préparation des modèles IA embarqués..."
            shutil.copytree(str(embarques), str(propre), dirs_exist_ok=True)
            logger.info(f"[Ollama] Modèles embarqués recopiés vers {propre}")
            return propre
        except Exception as e:
            logger.warning(f"[Ollama] Copie des modèles embarqués impossible ({e}) — "
                           f"utilisation en lecture seule")
            return Path(embarques)

    def est_disponible(self) -> bool:
        """Ollama est utilisable, quelle que soit son origine (app ou système)."""
        return self.binaire() is not None

    @staticmethod
    def serveur_repond(timeout: float = 2.0) -> bool:
        """Un serveur Ollama (externe OU embarqué) répond-il sur le port ?

        Sondé fréquemment par l'UI : on ferme explicitement la connexion
        (context manager) pour ne pas laisser de sockets ouverts. Une réponse
        HTTP d'erreur prouve qu'un serveur est BIEN là (port occupé) — c'est ce
        qui compte pour décider d'un démarrage ou d'une purge.
        """
        from config import OLLAMA_URL
        try:
            with _opener().open(f"{OLLAMA_URL}/api/tags", timeout=timeout):
                return True
        except urllib.error.HTTPError:
            return True          # serveur présent, mais renvoie une erreur
        except Exception:
            return False         # réellement injoignable

    def get_status(self) -> Dict[str, Any]:
        systeme = self.ollama_systeme()
        return {
            "installe":     self.est_installe(),
            # Livré avec l'application (aucune installation possible à faire)
            "embarque":     self.ollama_livre_avec_lapp() is not None,
            # Ollama installé par l'UTILISATEUR (ollama.com, brew, apt) : il
            # compte autant que l'embarqué, et évite un téléchargement inutile.
            "installe_systeme": str(systeme) if systeme else "",
            "disponible":   self.est_disponible(),
            "serveur_actif": self.serveur_repond(),
            "processus_gere": self._proc is not None and self._proc.poll() is None,
            "etape":        self.etape,
            "progression":  round(self.progression, 1),
            "detail":       self.detail,
            "erreur":       self.erreur,
            "dossier":      str(self.dossier),
            "journal":      self._queue_log(200),
        }

    # ── Installation automatique (thread d'arrière-plan) ──────────────
    def installer_async(self, modele: str = None, force: bool = False) -> Dict[str, Any]:
        # Par défaut, installer LE modèle réellement utilisé par les agents
        # (OLLAMA_MODEL). Un défaut codé en dur « llama3.2 » divergerait du
        # modèle configuré → les agents demanderaient un modèle non téléchargé
        # et retomberaient SILENCIEUSEMENT en simulation.
        if not modele:
            try:
                from config import ollama_model_actif
                modele = ollama_model_actif() or "llama3.2"
            except Exception:
                modele = "llama3.2"
        with self._lock:
            if self.etape in ("telechargement", "extraction", "demarrage", "modele"):
                return {"success": False, "error": "Installation déjà en cours"}
            self.etape = "telechargement"
            self.progression = 0.0
            self.erreur = ""
        if force:
            # Réinstallation complète : purge le dossier (binaire suspect,
            # quarantaine antivirus, extraction incomplète...). Il faut d'abord
            # arrêter TOUT serveur Ollama : sous Windows, supprimer un
            # ollama.exe encore en cours d'exécution échoue silencieusement
            # (purge partielle → extraction ensuite bloquée sur un fichier
            # verrouillé). On refuse proprement plutôt que de purger à moitié.
            import shutil
            if not self._arreter_serveur_ollama():
                with self._lock:
                    self.etape = "erreur"
                    self.erreur = ("Un serveur Ollama est encore actif et verrouille "
                                   "les fichiers. Fermez Ollama puis relancez la "
                                   "réinstallation.")
                return {"success": False, "error": self.erreur}
            try:
                shutil.rmtree(self.dossier)   # PAS ignore_errors : on veut voir l'échec
            except FileNotFoundError:
                pass
            except OSError as e:
                with self._lock:
                    self.etape = "erreur"
                    self.erreur = (f"Purge du dossier Ollama impossible ({e}). "
                                   "Fermez Ollama (ou l'antivirus) et réessayez.")
                return {"success": False, "error": self.erreur}
            self._resoudre_exe()
            logger.info("[Ollama embarqué] Réinstallation complète demandée — dossier purgé")
        threading.Thread(target=self._installer, args=(modele,), daemon=True).start()
        return {"success": True}

    def _arreter_serveur_ollama(self) -> bool:
        """Arrête tout serveur Ollama et attend la libération du port. Retourne
        True si plus aucun serveur ne répond (dossier alors purgeable)."""
        self.arreter()                       # notre propre process en priorité
        if not self.serveur_repond():
            return True
        # Un serveur d'une exécution précédente tient encore le port : Ollama
        # n'a pas d'API d'arrêt → best-effort selon l'OS.
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"],
                               capture_output=True, timeout=10)
            else:
                subprocess.run(["pkill", "-f", "ollama"], capture_output=True, timeout=10)
        except Exception:
            pass
        for _ in range(10):                  # attendre la libération (~10 s)
            if not self.serveur_repond():
                return True
            time.sleep(1)
        return not self.serveur_repond()

    @staticmethod
    def modele_present(modele: str) -> bool:
        """Le modèle est-il DÉJÀ disponible sur le serveur en cours ?

        Décisif pour les modèles embarqués au build : sans ce test, l'app
        relançait un `pull` de plusieurs gigaoctets sur un modèle qu'elle
        venait de livrer elle-même.
        """
        from config import OLLAMA_URL
        try:
            with _opener().open(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
                noms = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        except Exception:
            return False
        racine = (modele or "").split(":")[0]
        return any(racine and racine in n for n in noms)

    def _installer(self, modele: str):
        try:
            # Ollama externe déjà actif → rien à télécharger, juste le modèle
            if self.serveur_repond():
                self._tirer_modele(modele)
                return
            # `binaire()` couvre les trois origines : embarqué au build,
            # déjà téléchargé, installé sur la machine. Le test portait avant
            # sur le seul binaire téléchargé — un exe livré avec Ollama
            # embarqué relançait donc un téléchargement de 1 Go pour rien.
            if self.binaire() is None:
                self._telecharger()
                self._extraire()
            self.etape = "demarrage"
            self.detail = ("Démarrage du serveur IA local... (le premier démarrage "
                           "peut prendre 1-2 min : l'antivirus analyse les fichiers)")
            # 150s : au premier lancement, Windows Defender scanne ~1 Go de
            # binaires fraîchement extraits — 30s ne suffisaient pas.
            if not self.demarrer(attente_max=150):
                raise RuntimeError(self._erreur_demarrage
                                   or "Le serveur Ollama embarqué n'a pas démarré")
            self._tirer_modele(modele)
        except Exception as e:
            logger.error(f"[Ollama embarqué] Installation : {e}")
            self.etape = "erreur"
            self.erreur = str(e)

    def _telecharger(self):
        systeme = platform.system()
        urls = urls_pour_plateforme()
        if not urls:
            raise RuntimeError(
                f"Architecture non gérée : {systeme}/{_architecture()}. "
                f"Installez Ollama manuellement depuis https://ollama.com/download")
        self.dossier.mkdir(parents=True, exist_ok=True)

        self.etape = "telechargement"
        self.detail = "Téléchargement d'Ollama (~1 Go)..."
        derniere_erreur = None
        for url in urls:
            archive = self.dossier / ("ollama.zip" if url.endswith(".zip") else "ollama.tgz")
            logger.info(f"[Ollama embarqué] Téléchargement {url}")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "AgenceNumerique"})
                # Magasins de certificats en cascade : un antivirus qui
                # inspecte le HTTPS faisait échouer ce téléchargement sur
                # « CERTIFICATE_VERIFY_FAILED », sans cause nommée.
                from utils import reseau
                with reseau.ouvrir(req, timeout=60) as resp, open(archive, "wb") as f:
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
                # Contrôle de complétude : une coupure « propre » (proxy qui
                # ferme la connexion) fait sortir la boucle sans exception et
                # laisserait une archive TRONQUÉE, qui échouerait plus tard à
                # l'extraction avec un message incompréhensible.
                if total and lu < total:
                    raise RuntimeError(
                        f"téléchargement incomplet ({lu}/{total} octets)")
                self._archive = archive
                return
            except Exception as e:
                derniere_erreur = e
                archive.unlink(missing_ok=True)
        raise RuntimeError(
            f"Téléchargement impossible ({derniere_erreur}). "
            "Installez Ollama manuellement depuis ollama.com/download "
            "puis cliquez « Vérifier à nouveau »."
        )

    def _extraire(self):
        self.etape = "extraction"
        self.progression = 0.0
        self.detail = "Extraction..."
        archive = getattr(self, "_archive", None)
        if archive is None or not Path(archive).exists():
            raise RuntimeError("Archive introuvable")
        if str(archive).endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(self.dossier)
        else:
            with tarfile.open(archive) as t:
                # filter="data" : refuse les chemins absolus / « .. » et les
                # bits setuid. Obligatoire à partir de Python 3.14 (l'extraction
                # sans filtre y lève une erreur) et supprime le
                # DeprecationWarning sur 3.12/3.13. Repli pour Python < 3.12.
                try:
                    t.extractall(self.dossier, filter="data")
                except TypeError:
                    t.extractall(self.dossier)
        Path(archive).unlink(missing_ok=True)
        # binaire exécutable sur Linux/Mac
        if platform.system() != "Windows":
            for cand in (self.dossier / "bin" / "ollama", self.dossier / "ollama"):
                if cand.exists():
                    cand.chmod(0o755)
                    self.exe = cand
                    break
        else:
            self.exe = self.dossier / "ollama.exe"

    # ── Serveur ───────────────────────────────────────────────────────
    def demarrer(self, attente_max: float = 45.0) -> bool:
        """Démarre `ollama serve` en arrière-plan si nécessaire."""
        self._erreur_demarrage = ""
        if self.serveur_repond():
            return True
        # ré-résoudre le chemin (install précédente) SANS réinitialiser l'état :
        # __init__ effacerait la progression en cours.
        self._resoudre_exe()
        exe = self.binaire()
        if exe is None or not Path(exe).exists():
            self._erreur_demarrage = "Binaire Ollama introuvable"
            return False
        # « Géré par l'application » = embarqué au build OU téléchargé par
        # elle. Dans les deux cas ses modèles vivent dans le dossier de l'app.
        gere_par_lapp = (self.ollama_livre_avec_lapp() is not None
                         or str(exe) == str(self.exe))

        env = os.environ.copy()
        env.setdefault("OLLAMA_HOST", "127.0.0.1:11434")
        if gere_par_lapp:
            # Binaire embarqué ou téléchargé par l'app : ses modèles vivent
            # avec l'application, pour ne rien éparpiller sur la machine.
            env.setdefault("OLLAMA_MODELS", str(self.dossier_modeles()))
        # Ollama SYSTÈME : on ne touche PAS à OLLAMA_MODELS. Forcer le dossier
        # de l'app masquerait les modèles que l'utilisateur a déjà tirés
        # (« ollama pull llama3.2 ») : l'app les croirait absents et
        # relancerait un téléchargement de plusieurs gigaoctets pour rien.

        # Sortie du serveur capturée dans un log : indispensable pour
        # diagnostiquer un échec (avant : DEVNULL → panne invisible).
        # mkdir : le dossier de travail n'existe pas forcément — c'est le cas
        # d'un binaire EMBARQUÉ (rien n'a jamais été téléchargé) comme d'un
        # Ollama système. Sans lui, `open()` levait FileNotFoundError et le
        # démarrage échouait avant même d'avoir lancé quoi que ce soit.
        self.dossier.mkdir(parents=True, exist_ok=True)
        self._log_path = self.dossier / "serve.log"
        log_f = open(self._log_path, "ab")

        kwargs: Dict[str, Any] = {
            "env": env,
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        origine = "embarqué" if gere_par_lapp else "système"
        logger.info(f"[Ollama] Démarrage du serveur {origine} : {exe} serve")
        try:
            self._proc = subprocess.Popen([str(exe), "serve"], **kwargs)
        except Exception as e:
            self._erreur_demarrage = f"Lancement impossible : {e}"
            return False
        finally:
            # Le descripteur est DUPLIQUÉ dans l'enfant par Popen : on referme
            # notre copie dans tous les cas. Sans ce close, chaque tentative de
            # démarrage (réinstallation, reprise après échec) fuitait un
            # descripteur sur serve.log — et sous Windows le fichier restait
            # verrouillé, empêchant la purge lors d'une réinstallation forcée.
            log_f.close()
        # Le serveur embarqué s'arrête proprement avec l'application.
        # Enregistré UNE SEULE FOIS : atexit ne déduplique pas, et redémarrer
        # Ollama (réinstallation, reprise) empilait les rappels.
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
                    f"Détail : {self._queue_log()}"
                )
                return False
            time.sleep(0.8)
        self._erreur_demarrage = (
            f"Pas de réponse après {int(attente_max)}s. "
            f"Journal : {self._queue_log()}"
        )
        return False

    def _queue_log(self, max_chars: int = 300) -> str:
        """Dernières lignes du journal du serveur (diagnostic)."""
        try:
            data = (self.dossier / "serve.log").read_bytes()
            texte = data[-2000:].decode("utf-8", errors="replace")
            lignes = [l for l in texte.splitlines() if l.strip()]
            return " | ".join(lignes[-3:])[-max_chars:] or "(journal vide)"
        except Exception:
            return "(journal indisponible)"

    def demarrer_si_possible(self):
        """Appelé au boot de l'app : démarre Ollama s'il est installé — qu'il
        vienne de l'application OU d'une installation classique de la machine.

        La condition portait avant sur `est_installe()` seul, donc sur le
        binaire embarqué UNIQUEMENT : un Ollama installé depuis ollama.com mais
        pas encore lancé n'était jamais démarré, et l'application affichait
        « IA locale non détectée » alors qu'elle n'avait qu'à le lancer.
        """
        try:
            if self.serveur_repond():
                return
            if self.binaire() is not None:
                self.demarrer(attente_max=20)
        except Exception as e:
            logger.warning(f"[Ollama] Démarrage auto : {e}")

    def arreter(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    # ── Modèle ────────────────────────────────────────────────────────
    def _tirer_modele(self, modele: str):
        from config import OLLAMA_URL
        # Modèle déjà là (embarqué au build, ou tiré lors d'une session
        # précédente) : rien à télécharger.
        if self.modele_present(modele):
            self.etape = "pret"
            self.progression = 100.0
            self.detail = f"Modèle {modele} déjà présent — IA locale opérationnelle"
            logger.info(f"[Ollama] Modèle {modele} déjà disponible")
            return
        self.etape = "modele"
        self.progression = 0.0
        self.detail = f"Téléchargement du modèle {modele} (~2 Go)..."
        logger.info(f"[Ollama embarqué] pull {modele}")

        payload = json.dumps({"model": modele}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/pull", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        # flux de progression ligne par ligne
        with _opener().open(req, timeout=7200) as resp:
            for ligne in resp:
                try:
                    info = json.loads(ligne)
                except Exception:
                    continue
                total = info.get("total") or 0
                fait  = info.get("completed") or 0
                if total:
                    self.progression = fait / total * 100
                statut = info.get("status", "")
                if statut:
                    self.detail = f"{modele} : {statut}"
                if info.get("error"):
                    raise RuntimeError(info["error"])

        self.etape = "pret"
        self.progression = 100.0
        self.detail = f"Modèle {modele} prêt — IA locale opérationnelle"


# ── Singleton ─────────────────────────────────────────────────────────
_embedded: Optional[OllamaEmbedded] = None
_embedded_lock = threading.Lock()


def get_ollama_embedded() -> OllamaEmbedded:
    global _embedded
    if _embedded is None:
        with _embedded_lock:
            if _embedded is None:
                _embedded = OllamaEmbedded()
    return _embedded
