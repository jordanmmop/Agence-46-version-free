#!/usr/bin/env python3
"""Produit les visuels de la fiche Microsoft Store (`packaging/store/`).

Partner Center > Descriptions dans le Store > Logos Windows Store réclame une
« image d'affiche », en 720x1080 ou 1440x2160. Elle sert aux mises en avant du
Store, où elle est affichée en portrait, parfois recadrée et souvent surmontée
du nom de l'application par le Store lui-même.

ATTENTION AU LIBELLÉ. L'interface de Partner Center annonce ce visuel comme du
« 9:16 ». Ce n'en est pas : 720x1080 vaut 2:3 (0,667), là où du 9:16 donnerait
720x1280 (0,5625). Ce sont les DIMENSIONS listées qui sont validées, pas le
rapport annoncé — une image réellement en 9:16 serait refusée. Les fichiers
sont donc nommés d'après leurs dimensions, et non d'après ce libellé.

Trois règles ont guidé la composition, et elles expliquent ce qui suit :

1. MARGE DE SÉCURITÉ. Le Store peut rogner les bords et poser ses propres
   libellés par-dessus. Tout ce qui compte reste donc dans les 78 % centraux ;
   un titre collé au bord disparaîtrait sur la moitié des emplacements.

2. AUCUNE PROMESSE CHIFFRÉE. Le produit passe des ordres réels : une courbe
   ascendante, un pourcentage ou un montant sur l'affiche seraient lus comme
   une promesse de gain. Le motif de fond est volontairement abstrait — une
   constellation de 46 points, un par agent — et ne représente aucun cours.

3. LISIBLE EN PETIT. L'affiche est souvent vue en vignette : peu de texte,
   fort contraste, et rien sous la taille perceptible à cette échelle.

    python tools/generer_visuels_store.py             # les deux tailles
    python tools/generer_visuels_store.py --verifier  # contrôle sans réécrire

Les fichiers produits sont VERSIONNÉS, comme `installer/agence.ico` et les
assets MSIX : la fiche Store doit pouvoir être refaite à l'identique dans six
mois, et la compilation ne dépend jamais de Pillow.
"""
import argparse
import math
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
LOGO = RACINE / "frontend" / "icons" / "logo.png"
CIBLE = RACINE / "packaging" / "store"

# Les visuels réclamés par Partner Center, et les proportions de chacun.
#
# Une composition ne se transpose PAS d'un format à l'autre : le portrait a de
# la hauteur à distribuer, le carré n'en a pas. Reprendre telles quelles les
# valeurs du portrait dans un carré donne un logo qui mange la moitié du cadre
# et un texte à l'étroit. Chaque format a donc ses propres proportions, toutes
# exprimées en fraction de la largeur (horizontal) ou de la hauteur (vertical).
VISUELS = {
    "affiche": {
        # 720x1080 vaut 2:3, malgré le « 9:16 » affiché par l'interface.
        "tailles": [(720, 1080), (1440, 2160)],
        "logo": 0.42,        # côté du logo, en fraction de la largeur
        "centre": 0.375,     # centre du logo et du halo, en fraction de la hauteur
        "titre": 0.625,      # sommet du titre
        "corps_titre": 0.105,
        "corps_accroche": 0.046,
        "corps_detail": 0.0285,
        "detail": True,
        "texte": True,
        "constellation": True,
    },
    "logo": {
        # Petits visuels carrés du Store : 300x300 et 150x150.
        #
        # AUCUN TEXTE, volontairement. À cette échelle un titre serait
        # illisible, et le Store affiche de toute façon le nom de
        # l'application juste à côté : le répéter dans l'image ne ferait que
        # voler la place de l'icône. Pas de constellation non plus — 46 points
        # sur 300 pixels ne se lisent plus comme une structure, seulement
        # comme du bruit.
        "tailles": [(300, 300), (150, 150), (71, 71)],
        "logo": 0.88,
        "centre": 0.5,
        "titre": 0.0,
        "corps_titre": 0.0,
        "corps_accroche": 0.0,
        "corps_detail": 0.0,
        "detail": False,
        "texte": False,
        "constellation": False,
    },
    "zone": {
        "tailles": [(1080, 1080), (2160, 2160)],
        # Carré : le logo est proportionnellement plus petit et le texte plus
        # resserré, sans quoi rien ne respire.
        "logo": 0.34,
        "centre": 0.355,
        "titre": 0.615,
        "corps_titre": 0.082,
        "corps_accroche": 0.038,
        "corps_detail": 0.0235,
        "detail": True,
        "texte": True,
        "constellation": True,
    },
}

# Palette de l'application (frontend/css/style.css et l'écran de chargement).
FOND_HAUT = (12, 18, 36)       # #0c1224
FOND_BAS = (5, 8, 16)          # #050810
ACCENT = (91, 141, 239)        # #5b8def — le bleu des boutons
ACCENT_CLAIR = (100, 181, 246)  # #64b5f6 — le bleu des titres
BLANC = (255, 255, 255)
GRIS = (150, 168, 199)

TITRE = "Agence 46"
ACCROCHE = "46 agents. Une décision."
DETAIL = "IA locale  ·  MetaTrader 5  ·  aucune donnée envoyée"

POLICES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]


def _police(gras: bool, taille: int):
    from PIL import ImageFont
    candidats = [p for p in POLICES if ("Bold" in p or "b.ttf" in p) == gras]
    for chemin in candidats + POLICES:
        try:
            return ImageFont.truetype(chemin, taille)
        except Exception:
            continue
    return ImageFont.load_default()


def _centrer(dessin, texte, police, y, couleur, largeur):
    gauche, haut, droite, bas = dessin.textbbox((0, 0), texte, font=police)
    dessin.text(((largeur - (droite - gauche)) / 2 - gauche, y - haut),
                texte, font=police, fill=couleur)
    return bas - haut


def _fond(largeur, hauteur, centre):
    """Dégradé vertical + halo derrière le logo.

    Un aplat uni paraît plat en vignette ; le dégradé donne de la profondeur
    sans introduire le moindre élément figuratif.
    """
    from PIL import Image, ImageDraw, ImageFilter

    image = Image.new("RGB", (largeur, hauteur), FOND_BAS)
    dessin = ImageDraw.Draw(image)
    for y in range(hauteur):
        t = y / max(hauteur - 1, 1)
        dessin.line([(0, y), (largeur, y)], fill=tuple(
            int(FOND_HAUT[i] + (FOND_BAS[i] - FOND_HAUT[i]) * t) for i in range(3)))

    # Halo : dessiné large puis flouté — un cercle net ferait « tache ».
    halo = Image.new("L", (largeur, hauteur), 0)
    d = ImageDraw.Draw(halo)
    cx, cy, r = largeur / 2, hauteur * centre, largeur * 0.46
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=70)
    halo = halo.filter(ImageFilter.GaussianBlur(largeur * 0.13))
    image = Image.composite(Image.new("RGB", (largeur, hauteur), ACCENT), image, halo)
    return image


def _constellation(image, largeur, hauteur, centre):
    """46 points : un par agent. Motif abstrait, aucun cours représenté.

    Disposés sur trois arcs concentriques plutôt qu'au hasard : un semis
    aléatoire ressemble à du bruit, des arcs se lisent comme une structure —
    ce que sont réellement les agents.
    """
    from PIL import Image, ImageDraw, ImageFilter

    couche = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
    d = ImageDraw.Draw(couche)
    cx, cy = largeur / 2, hauteur * centre
    restants = 46
    for anneau, (rayon, nombre, alpha, taille) in enumerate((
            (largeur * 0.30, 10, 55, largeur * 0.0055),
            (largeur * 0.40, 16, 40, largeur * 0.0045),
            (largeur * 0.50, 20, 26, largeur * 0.0038))):
        nombre = min(nombre, restants)
        restants -= nombre
        depart = anneau * 0.4
        for i in range(nombre):
            angle = depart + i * 2 * math.pi / nombre
            x = cx + rayon * math.cos(angle)
            y = cy + rayon * math.sin(angle) * 0.85
            d.ellipse([x - taille, y - taille, x + taille, y + taille],
                      fill=ACCENT_CLAIR + (alpha,))
    couche = couche.filter(ImageFilter.GaussianBlur(largeur * 0.0015))
    return Image.alpha_composite(image.convert("RGBA"), couche).convert("RGB")


def composer(largeur, hauteur, mise_en_page):
    from PIL import Image, ImageDraw

    centre = mise_en_page["centre"]
    image = _fond(largeur, hauteur, centre)
    if mise_en_page.get("constellation", True):
        image = _constellation(image, largeur, hauteur, centre)
    dessin = ImageDraw.Draw(image)

    # ── Logo ─────────────────────────────────────────────────────────
    with Image.open(LOGO) as brut:
        logo = brut.convert("RGBA")
    cote = int(largeur * mise_en_page["logo"])
    logo = logo.resize((cote, cote), Image.LANCZOS)
    image.paste(logo, ((largeur - cote) // 2, int(hauteur * centre - cote / 2)), logo)

    # ── Titre, filet, accroche ───────────────────────────────────────
    if not mise_en_page.get("texte", True):
        return image

    y = hauteur * mise_en_page["titre"]
    y += _centrer(dessin, TITRE,
                  _police(True, int(largeur * mise_en_page["corps_titre"])),
                  y, BLANC, largeur) + hauteur * 0.030

    filet = largeur * 0.085
    epaisseur = max(2, int(hauteur * 0.0028))
    dessin.rectangle([(largeur - filet) / 2, y, (largeur + filet) / 2, y + epaisseur],
                     fill=ACCENT)
    y += epaisseur + hauteur * 0.030

    y += _centrer(dessin, ACCROCHE,
                  _police(False, int(largeur * mise_en_page["corps_accroche"])),
                  y, ACCENT_CLAIR, largeur) + hauteur * 0.022
    if mise_en_page["detail"]:
        _centrer(dessin, DETAIL,
                 _police(False, int(largeur * mise_en_page["corps_detail"])),
                 y, GRIS, largeur)
    return image


def verifier() -> int:
    try:
        from PIL import Image
    except ImportError:
        Image = None
    manquants = []
    total = 0
    for nom, mise_en_page in VISUELS.items():
        for largeur, hauteur in mise_en_page["tailles"]:
            total += 1
            chemin = CIBLE / f"{nom}-{largeur}x{hauteur}.png"
            if not chemin.is_file():
                manquants.append(f"{chemin.name} (absent)")
            elif Image is not None:
                with Image.open(chemin) as im:
                    if im.size != (largeur, hauteur):
                        manquants.append(f"{chemin.name} ({im.size[0]}x{im.size[1]})")
    if manquants:
        print("[!] à régénérer : " + ", ".join(manquants))
        return 1
    print(f"[OK] {total} visuels Store présents et aux bonnes dimensions")
    return 0


def main() -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("[ERREUR] Pillow requis : python -m pip install pillow")
        return 1
    if not LOGO.is_file():
        print(f"[ERREUR] logo introuvable : {LOGO}")
        return 1

    CIBLE.mkdir(parents=True, exist_ok=True)
    for nom, mise_en_page in VISUELS.items():
        for largeur, hauteur in mise_en_page["tailles"]:
            chemin = CIBLE / f"{nom}-{largeur}x{hauteur}.png"
            composer(largeur, hauteur, mise_en_page).save(chemin, "PNG", optimize=True)
            print(f"[OK] {chemin.relative_to(RACINE)}  "
                  f"({chemin.stat().st_size / 1024:.0f} Ko)")
    return 0


if __name__ == "__main__":
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--verifier", action="store_true")
    args = parseur.parse_args()
    sys.exit(verifier() if args.verifier else main())
