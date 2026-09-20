#!/usr/bin/env python3
"""Prépare les ressources de l'installeur Windows, AVANT Inno Setup.

Deux choses, toutes deux facultatives — l'installeur se compile sans elles :

1. `installer/agence.ico` : l'icône du logiciel (versionnée dans le dépôt, ce
   script ne fait que la régénérer si le logo a changé) ;

2. `installer/redist/MicrosoftEdgeWebview2Setup.exe` : l'amorceur du composant
   « Microsoft Edge WebView2 Runtime ». C'est lui qui affiche la fenêtre de
   l'application (pywebview s'appuie dessus). Présent d'origine sur Windows 11
   et sur un Windows 10 à jour, il peut manquer sur une machine fraîchement
   installée. L'embarquer permet une installation SANS connexion ; sans lui,
   l'installeur le télécharge à la volée, et si le téléchargement échoue
   l'application se rabat sur le navigateur par défaut.

    python tools/preparer_installeur.py            # icône + WebView2
    python tools/preparer_installeur.py --verifier # état, sans rien faire
    python tools/preparer_installeur.py --sans-webview2

Le script est IDEMPOTENT et ne fait jamais échouer un build : un
téléchargement impossible (hors ligne, miroir bloqué) est signalé, pas fatal.
"""
import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
INSTALLER = RACINE / "installer"
ICONE = INSTALLER / "agence.ico"
REDIST = INSTALLER / "redist"
WEBVIEW2 = REDIST / "MicrosoftEdgeWebview2Setup.exe"
# Lien permanent publié par Microsoft pour l'amorceur « Evergreen ».
URL_WEBVIEW2 = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
# L'amorceur pèse ~1,8 Mo. Nettement en dessous : ce n'est pas le binaire mais
# une page d'erreur ou une redirection captée par un portail réseau.
TAILLE_MINIMALE = 500 * 1024

if str(RACINE / "python") not in sys.path:
    sys.path.insert(0, str(RACINE / "python"))


def info(msg):  print(f"  {msg}", flush=True)
def ok(msg):    print(f"  [OK] {msg}", flush=True)
def warn(msg):  print(f"  [!] {msg}", flush=True)


def preparer_icone() -> bool:
    if ICONE.is_file() and ICONE.stat().st_size > 0:
        ok(f"icone deja presente ({ICONE.stat().st_size / 1024:.0f} Ko)")
        return True
    info("generation de l'icone...")
    try:
        from generer_icone import main as generer
    except ImportError:
        sys.path.insert(0, str(RACINE / "tools"))
        from generer_icone import main as generer
    if generer() == 0:
        return True
    warn("icone non generee (Pillow absent ?) — l'installeur utilisera "
         "l'icone par defaut d'Inno Setup")
    return False


def _valide(chemin: Path) -> bool:
    """Un exécutable Windows, et pas une page d'erreur enregistrée sous ce nom."""
    try:
        if chemin.stat().st_size < TAILLE_MINIMALE:
            return False
        with open(chemin, "rb") as f:
            return f.read(2) == b"MZ"
    except Exception:
        return False


def preparer_webview2() -> bool:
    if WEBVIEW2.is_file():
        if _valide(WEBVIEW2):
            ok(f"WebView2 deja present ({WEBVIEW2.stat().st_size / 1048576:.1f} Mo)")
            return True
        warn("amorceur WebView2 invalide — nouveau telechargement")
        WEBVIEW2.unlink(missing_ok=True)

    info(f"telechargement de l'amorceur WebView2 depuis {URL_WEBVIEW2}")
    REDIST.mkdir(parents=True, exist_ok=True)
    partiel = WEBVIEW2.with_suffix(".exe.part")
    try:
        import urllib.request
        requete = urllib.request.Request(
            URL_WEBVIEW2, headers={"User-Agent": "AgenceNumerique/build"})
        # Même magasin de certificats en cascade que le reste du build : un
        # antivirus qui inspecte le HTTPS présente sa propre autorité, et un
        # urlopen nu échouerait sur CERTIFICATE_VERIFY_FAILED.
        try:
            from utils import reseau
            flux = reseau.ouvrir(requete, timeout=120)
        except Exception:
            flux = urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open(requete, timeout=120)
        with flux as reponse, open(partiel, "wb") as f:
            while True:
                bloc = reponse.read(256 * 1024)
                if not bloc:
                    break
                f.write(bloc)
        if not _valide(partiel):
            raise RuntimeError("contenu telecharge inattendu (pas un .exe Windows)")
        partiel.replace(WEBVIEW2)
        ok(f"WebView2 embarque ({WEBVIEW2.stat().st_size / 1048576:.1f} Mo) "
           f"— installation possible hors ligne")
        return True
    except Exception as e:
        partiel.unlink(missing_ok=True)
        warn(f"telechargement impossible ({type(e).__name__}: {e})")
        info("l'installeur recuperera WebView2 a l'installation, et")
        info("l'application se rabattra sur le navigateur en cas d'echec")
        return False


def etat() -> int:
    print("\n=== Ressources de l'installeur ===")
    for chemin, libelle in ((ICONE, "icone"), (WEBVIEW2, "amorceur WebView2")):
        if chemin.is_file():
            ok(f"{libelle} : {chemin.relative_to(RACINE)} "
               f"({chemin.stat().st_size / 1024:.0f} Ko)")
        else:
            warn(f"{libelle} : absent ({chemin.relative_to(RACINE)})")
    return 0


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--verifier", action="store_true",
                         help="affiche l'etat sans rien telecharger")
    parseur.add_argument("--sans-webview2", action="store_true",
                         help="ne pas embarquer l'amorceur WebView2")
    args, _ = parseur.parse_known_args()

    if args.verifier:
        return etat()

    print("\n=== Ressources de l'installeur ===")
    preparer_icone()
    if not args.sans_webview2:
        preparer_webview2()
    # Aucune de ces ressources n'est indispensable : le code de retour reste 0
    # pour ne jamais interrompre une chaine de build a cause d'un composant
    # optionnel.
    return 0


if __name__ == "__main__":
    sys.exit(main())
