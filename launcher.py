"""
Agence Numérique Financière — Lanceur ALTERNATIF (fenêtre Tkinter simple).

Le point d'entrée OFFICIEL est app_window.py (compilé par agence.spec /
build_exe.bat) ; en local, utilisez plutôt start.py ou run.bat. Ce lanceur
autonome reste disponible pour un démarrage minimaliste sans pywebview.
"""
import os
import sys
import subprocess
import webbrowser
import threading
import time
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


# ── Fenêtre principale ───────────────────────────────────────────────────────

class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Agence Numérique Financière")
        self.resizable(False, False)
        self.configure(bg="#0a0e1a")
        self._backend_proc = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._quitter)
        self.after(100, self._demarrer)

    # ── UI ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 24, "pady": 8}
        BLUE, WHITE, GREY = "#3b82f6", "#e2e8f0", "#64748b"

        tk.Label(self, text="🏦", font=("Segoe UI Emoji", 40), bg="#0a0e1a", fg=WHITE).pack(pady=(28, 4))
        tk.Label(self, text="Agence Numérique Financière", font=("Segoe UI", 16, "bold"),
                 bg="#0a0e1a", fg=WHITE).pack()
        tk.Label(self, text="46 Agents IA de Trading", font=("Segoe UI", 10),
                 bg="#0a0e1a", fg=GREY).pack(pady=(2, 16))

        # Status
        self._status_var = tk.StringVar(value="Initialisation...")
        self._status_lbl = tk.Label(self, textvariable=self._status_var,
                                    font=("Segoe UI", 10), bg="#0a0e1a", fg=GREY, wraplength=360)
        self._status_lbl.pack(**pad)

        # Barre de progression
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("B.Horizontal.TProgressbar", troughcolor="#1a2235",
                        background=BLUE, bordercolor="#1a2235", lightcolor=BLUE, darkcolor=BLUE)
        self._progress = ttk.Progressbar(self, style="B.Horizontal.TProgressbar",
                                         length=360, mode="indeterminate")
        self._progress.pack(padx=24, pady=4)

        # Bouton ouvrir
        self._btn = tk.Button(self, text="Ouvrir le tableau de bord →",
                              font=("Segoe UI", 10, "bold"),
                              bg=BLUE, fg=WHITE, activebackground="#2563eb", activeforeground=WHITE,
                              relief="flat", cursor="hand2", state="disabled",
                              command=lambda: webbrowser.open("http://localhost:8000"))
        self._btn.pack(padx=24, pady=(8, 4), fill="x")

        tk.Label(self, text="http://localhost:8000", font=("Segoe UI", 8),
                 bg="#0a0e1a", fg=GREY).pack(pady=(0, 24))

        self.geometry("420x320")
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"+{x}+{y}")

    # ── Marshalling vers le thread Tk ──────────────────────────────────
    # Tkinter n'est PAS thread-safe : toute la préparation (_run) tourne dans
    # un thread de travail pour ne pas figer la fenêtre, mais chaque
    # modification de widget doit repartir sur le thread principal via
    # after(0, ...). Appelés directement depuis le thread, ces appels
    # produisaient des blocages et des « main thread is not in main loop ».
    def _ui(self, fn, *args):
        try:
            self.after(0, lambda: fn(*args))
        except RuntimeError:
            pass                      # fenêtre déjà détruite

    def _set_status(self, msg: str, color: str = "#64748b"):
        def _appliquer():
            self._status_var.set(msg)
            self._status_lbl.configure(fg=color)
        self._ui(_appliquer)

    # ── Démarrage ──────────────────────────────────────────────────────
    def _demarrer(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        # L'application est 100 % locale : plus aucune clé API à chercher, à
        # demander ni à enregistrer. Le moteur IA (Ollama ou Hermès) est
        # embarqué et démarré par le serveur lui-même.
        self._set_status("Préparation...")

        # 1. Répertoires
        (ROOT / "python" / "data").mkdir(parents=True, exist_ok=True)
        (ROOT / "logs").mkdir(exist_ok=True)

        # 2. Trouver Python
        python = self._trouver_python()
        if not python:
            def _abandonner():
                messagebox.showerror(
                    "Erreur",
                    "Python 3.10+ est requis.\nTéléchargez-le sur https://python.org")
                self.destroy()
            self._ui(_abandonner)
            return

        # 3. Installer les dépendances si nécessaire
        self._set_status("Vérification des dépendances...")
        self._installer_deps(python)

        # 4. Lancer le backend
        self._set_status("Démarrage du serveur...")
        self._ui(self._progress.start, 12)
        ok = self._lancer_backend(python)
        self._ui(self._progress.stop)

        if ok:
            self._set_status("✓ Serveur prêt — bienvenue !", "#10b981")
            self._ui(self._btn.configure, {"state": "normal"})
            webbrowser.open("http://localhost:8000")
        else:
            self._set_status("✗ Erreur de démarrage — voir logs/backend.log", "#ef4444")

    def _trouver_python(self) -> str:
        candidats = ["python", "python3"]
        # Dans un exe PyInstaller, sys.executable est l'exe lui-même :
        # le tester relancerait l'application.
        if not getattr(sys, "frozen", False):
            candidats.append(sys.executable)
        for cmd in candidats:
            try:
                r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and "Python 3" in r.stdout + r.stderr:
                    return cmd
            except Exception:
                pass
        return ""

    def _installer_deps(self, python: str):
        import hashlib
        reqs = [ROOT / "python" / "requirements.txt", ROOT / "backend" / "requirements.txt"]
        marker = ROOT / "python" / "data" / ".deps_installed"
        marker.parent.mkdir(parents=True, exist_ok=True)
        # Empreinte du CONTENU des requirements : une mise à jour qui ajoute une
        # dépendance change l'empreinte et relance l'installation (un marqueur
        # « ok » figé laissait sinon des dépendances manquantes après un update).
        contenu = b"".join(r.read_bytes() for r in reqs if r.exists())
        empreinte = hashlib.sha256(contenu).hexdigest()[:16]
        if marker.exists() and marker.read_text().strip() == empreinte:
            return
        args = [python, "-m", "pip", "install", "-q"]
        for r in reqs:
            if r.exists():
                args += ["-r", str(r)]
        try:
            # timeout large : pandas/numpy/scipy peuvent dépasser 180 s sur une
            # machine neuve. En cas d'échec, on remonte la sortie de pip.
            r = subprocess.run(args, capture_output=True, timeout=1200, text=True)
            if r.returncode == 0:
                marker.write_text(empreinte)
            else:
                msg = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
                self._set_status("⚠ Installation partielle des dépendances", "#d8a03d")
                print("[launcher] pip a échoué :\n" + "\n".join(msg))
        except subprocess.TimeoutExpired:
            self._set_status("⚠ Installation des dépendances trop longue — réessayez", "#d8a03d")
        except Exception as e:
            print(f"[launcher] installation dépendances : {e}")

    def _lancer_backend(self, python: str) -> bool:
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{ROOT / 'python'}{os.pathsep}{ROOT}"
        # « wb » : le fichier ne sert que de destination au processus enfant,
        # qui y écrit ses propres octets (UTF-8). L'ouvrir en mode texte
        # imposerait l'encodage de la console — cp1252 sous Windows — à un
        # flux qui ne passe pas par ce descripteur.
        log = open(ROOT / "logs" / "backend.log", "wb")
        try:
            self._backend_proc = subprocess.Popen(
                [python, "-m", "uvicorn", "backend.main:app",
                 "--host", "0.0.0.0", "--port", "8000", "--app-dir", str(ROOT)],
                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        finally:
            # Popen DUPLIQUE le descripteur dans l'enfant : on referme notre
            # copie. Sans ce close, le lanceur gardait le journal ouvert pour
            # toute sa durée de vie — et sous Windows le fichier restait
            # VERROUILLÉ, faisant échouer la réouverture au démarrage suivant.
            # Même correction que pour le serveur Ollama (ollama_embedded).
            log.close()
        # ProxyHandler({}) : sans lui, une variable HTTP_PROXY détourne l'appel
        # à localhost vers le proxy — le lanceur conclut alors que le backend
        # n'a pas démarré alors qu'il répond. Même convention que start.py.
        sonde = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(25):
            try:
                with sonde.open("http://localhost:8000/api/health", timeout=1):
                    pass
                return True
            except Exception:
                if self._backend_proc.poll() is not None:
                    return False
                time.sleep(1)
        return False

    def _quitter(self):
        if self._backend_proc:
            self._backend_proc.terminate()
        self.destroy()


# ── Point d'entrée ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = Launcher()
    app.mainloop()
