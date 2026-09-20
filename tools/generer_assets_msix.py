#!/usr/bin/env python3
"""Produit les images du paquet MSIX (`packaging/msix/Assets/`).

Windows n'affiche pas une icône MSIX « à la demande » : il lit des fichiers
aux dimensions EXACTES déclarées dans le manifeste. Une image manquante ou de
mauvaise taille ne dégrade pas l'affichage — elle fait ÉCHOUER `makeappx` ou,
pire, passe la compilation et donne une vignette vide dans le menu Démarrer et
un carré gris dans le Microsoft Store.

    python tools/generer_assets_msix.py            # (re)génère tout
    python tools/generer_assets_msix.py --verifier # état, sans rien écrire

Les fichiers produits sont VERSIONNÉS, exactement comme `installer/agence.ico` :
la chaîne de compilation (locale ou CI) ne doit pas dépendre de Pillow. Ce
script ne sert qu'à les régénérer quand le logo change.

Deux familles d'images, et la différence compte :

- les logos « plated » (Square150x150Logo, Wide310x150Logo…) sont posés par
  Windows sur une tuile de couleur (`BackgroundColor` du manifeste) : le logo
  est centré avec une marge, sur fond transparent ;
- les variantes `altform-unplated` (barre des tâches, Alt-Tab) sont affichées
  SANS tuile, directement sur le bureau. Elles doivent remplir davantage le
  cadre, sinon l'icône paraît minuscule à côté des autres.
"""
import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SOURCES = [
    RACINE / "frontend" / "icons" / "logo.png",
    RACINE / "frontend" / "icons" / "icon-512.png",
    RACINE / "frontend" / "icons" / "icon-192.png",
]
CIBLE = RACINE / "packaging" / "msix" / "Assets"

# (nom de fichier, largeur, hauteur, part du cadre occupée par le logo)
#
# Les noms ne sont PAS libres : ce sont ceux référencés par AppxManifest.xml.
# Les variantes « targetsize-* » sont les icônes de la barre des tâches et de
# l'explorateur ; Windows choisit la plus proche de la taille demandée.
ASSETS = [
    # Vitrine du Microsoft Store et « Applications installées »
    ("StoreLogo.png",                    50,  50,  0.88),
    # Tuiles du menu Démarrer
    ("Square44x44Logo.png",              44,  44,  0.88),
    ("Square71x71Logo.png",              71,  71,  0.82),
    ("Square150x150Logo.png",           150, 150,  0.72),
    ("Square310x310Logo.png",           310, 310,  0.66),
    ("Wide310x150Logo.png",             310, 150,  0.62),
    # Écran de démarrage (affiché par Windows pendant l'activation)
    ("SplashScreen.png",                620, 300,  0.52),
    # Barre des tâches / Alt-Tab : pas de tuile derrière, on remplit plus.
    ("Square44x44Logo.targetsize-16.png",       16, 16, 1.0),
    ("Square44x44Logo.targetsize-24.png",       24, 24, 1.0),
    ("Square44x44Logo.targetsize-32.png",       32, 32, 1.0),
    ("Square44x44Logo.targetsize-48.png",       48, 48, 1.0),
    ("Square44x44Logo.targetsize-256.png",     256, 256, 1.0),
    ("Square44x44Logo.altform-unplated_targetsize-16.png",   16, 16, 1.0),
    ("Square44x44Logo.altform-unplated_targetsize-24.png",   24, 24, 1.0),
    ("Square44x44Logo.altform-unplated_targetsize-32.png",   32, 32, 1.0),
    ("Square44x44Logo.altform-unplated_targetsize-48.png",   48, 48, 1.0),
    ("Square44x44Logo.altform-unplated_targetsize-256.png", 256, 256, 1.0),
]


def source() -> Path:
    trouvee = next((p for p in SOURCES if p.is_file()), None)
    if trouvee is None:
        raise SystemExit(
            "[ERREUR] Aucune image source : " +
            ", ".join(str(p.relative_to(RACINE)) for p in SOURCES))
    return trouvee


def verifier() -> int:
    """Liste ce qui manque ou n'a pas la bonne taille. Ne réécrit rien."""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    manquants = []
    for nom, largeur, hauteur, _ in ASSETS:
        chemin = CIBLE / nom
        if not chemin.is_file():
            manquants.append(f"{nom} (absent)")
            continue
        if Image is None:
            continue
        with Image.open(chemin) as im:
            if im.size != (largeur, hauteur):
                manquants.append(f"{nom} ({im.size[0]}x{im.size[1]} "
                                 f"au lieu de {largeur}x{hauteur})")
    if manquants:
        print(f"[!] {len(manquants)} asset(s) à régénérer :")
        for m in manquants:
            print(f"    - {m}")
        return 1
    print(f"[OK] {len(ASSETS)} assets MSIX présents et aux bonnes dimensions")
    return 0


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("[ERREUR] Pillow requis : python -m pip install pillow")
        return 1

    origine = source()
    CIBLE.mkdir(parents=True, exist_ok=True)
    with Image.open(origine) as brut:
        logo = brut.convert("RGBA")

    for nom, largeur, hauteur, part in ASSETS:
        # Le logo garde ses proportions et se pose au CENTRE d'un cadre
        # transparent aux dimensions exactes attendues par Windows. Le
        # déformer pour remplir un cadre 310x150 donnerait un logo écrasé.
        cote = int(min(largeur, hauteur) * part)
        cote = max(cote, 1)
        redimensionne = logo.resize((cote, cote), Image.LANCZOS)
        cadre = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
        cadre.paste(redimensionne,
                    ((largeur - cote) // 2, (hauteur - cote) // 2),
                    redimensionne)
        cadre.save(CIBLE / nom, "PNG", optimize=True)

    print(f"[OK] {len(ASSETS)} assets MSIX generes depuis "
          f"{origine.relative_to(RACINE)} vers {CIBLE.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--verifier", action="store_true",
                         help="controle les fichiers sans les regenerer")
    args = parseur.parse_args()
    sys.exit(verifier() if args.verifier else main())
