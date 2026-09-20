#!/usr/bin/env python3
"""Produit `installer/agence.ico` à partir des icônes du frontend.

Windows a besoin d'un `.ico` MULTI-RÉSOLUTIONS à trois endroits : l'exe
compilé, l'assistant d'installation et l'entrée « Applications installées ».
Sans lui, l'application s'affiche partout avec l'icône générique d'un
exécutable inconnu — ce que Windows présente exactement comme un logiciel
douteux, alors que les images existent déjà dans `frontend/icons/`.

    python tools/generer_icone.py            # (re)génère installer/agence.ico

Le fichier produit est VERSIONNÉ : la compilation ne dépend donc pas de
Pillow. Ce script ne sert qu'à le régénérer si le logo change.
"""
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SOURCES = [
    RACINE / "frontend" / "icons" / "icon-512.png",
    RACINE / "frontend" / "icons" / "logo.png",
    RACINE / "frontend" / "icons" / "icon-192.png",
]
CIBLE = RACINE / "installer" / "agence.ico"
# Tailles attendues par Windows : barre des tâches, bureau, grandes icônes de
# l'explorateur. Omettre 256 donne une icône floue dans l'affichage « très
# grandes icônes » et dans l'assistant d'installation.
TAILLES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("[ERREUR] Pillow requis : python -m pip install pillow")
        return 1

    source = next((p for p in SOURCES if p.is_file()), None)
    if source is None:
        print(f"[ERREUR] Aucune image source trouvée parmi : "
              f"{', '.join(str(p) for p in SOURCES)}")
        return 1

    image = Image.open(source).convert("RGBA")
    # Carré : une source rectangulaire serait déformée par Pillow, et le logo
    # apparaîtrait écrasé dans la barre des tâches.
    if image.width != image.height:
        cote = max(image.width, image.height)
        carre = Image.new("RGBA", (cote, cote), (0, 0, 0, 0))
        carre.paste(image, ((cote - image.width) // 2, (cote - image.height) // 2))
        image = carre

    CIBLE.parent.mkdir(parents=True, exist_ok=True)
    image.save(CIBLE, format="ICO", sizes=TAILLES)
    print(f"[OK] {CIBLE.relative_to(RACINE)} — source {source.name}, "
          f"{len(TAILLES)} résolutions, {CIBLE.stat().st_size / 1024:.0f} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
