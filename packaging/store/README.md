# Visuels de la fiche Microsoft Store

Images téléversées **à la main** dans Partner Center → *Descriptions dans le
Store* → **Logos Windows Store**. Elles ne font pas partie du paquet MSIX et
n'interviennent pas dans la compilation.

```
packaging/store/
  logo-300x300.png         petit visuel carré — icône seule, sans texte
  logo-150x150.png         idem — icône seule, sans texte
  logo-71x71.png           le plus petit — icône seule, sans texte
  affiche-720x1080.png     image d'affiche (portrait), taille standard
  affiche-1440x2160.png    la même, en haute densité
  zone-1080x1080.png       image de zone (carré), taille standard
  zone-2160x2160.png       la même, en haute densité
```

| Champ Partner Center | Fichier | Rapport réel |
|---|---|---|
| Logo du Store · petits carrés | `logo-300x300` · `logo-150x150` · `logo-71x71` | 1:1 |
| Image d'affiche | `affiche-720x1080` · `affiche-1440x2160` | 2:3 |
| Image de zone | `zone-1080x1080` · `zone-2160x2160` | 1:1 |

## Les petits carrés ne portent aucun texte

Ce sont les seuls à en être dépourvus, et c'est délibéré. À 300 pixels — et
plus encore à 150 ou 71 —, un titre serait illisible — et le Store affiche de toute façon le nom de
l'application juste à côté : le répéter dans l'image ne ferait que voler la
place de l'icône. La constellation de 46 points disparaît aussi : à cette
échelle elle ne se lit plus comme une structure, seulement comme du bruit.

## Régénérer

```bash
python tools/generer_visuels_store.py            # les deux tailles
python tools/generer_visuels_store.py --verifier # contrôle sans réécrire
```

Les fichiers sont **versionnés** — comme `installer/agence.ico` et les assets
MSIX — pour que la fiche Store puisse être refaite à l'identique dans six mois
sans dépendre de Pillow.

## Le libellé « 9:16 » de l'image d'affiche est trompeur

L'interface annonce l'image d'affiche comme du **9:16**. Ce n'en est pas :

| | Rapport |
|---|---|
| 720 × 1080 (exigé) | **2:3** — 0,667 |
| 720 × 1280 (vrai 9:16) | 0,5625 |

Ce sont les **dimensions** listées qui sont validées, pas le rapport annoncé.
Une image réellement en 9:16 serait refusée. Les fichiers portent donc leurs
dimensions dans leur nom, et non ce libellé.

Le « 1:1 » de l'image de zone, lui, est exact : 1080 × 1080 est bien un carré.

## Une composition par format, pas une transposition

Le portrait a de la hauteur à distribuer, le carré n'en a pas. Reprendre telles
quelles les proportions du portrait dans un carré donne un logo qui mange la
moitié du cadre et un texte à l'étroit. Chaque format a donc ses propres
valeurs dans `VISUELS` (taille du logo, hauteur du titre, corps des textes) —
le carré réduit le logo de 42 % à 34 % de la largeur et resserre les textes.

## Trois contraintes qui ont dicté la composition

1. **Marge de sécurité.** Le Store rogne les bords selon l'emplacement et pose
   parfois ses propres libellés par-dessus. Tout ce qui compte tient dans les
   78 % centraux.

2. **Aucune promesse chiffrée.** L'application passe des ordres réels : une
   courbe ascendante, un pourcentage ou un montant seraient lus comme une
   promesse de gain. Le motif de fond est abstrait — 46 points sur trois arcs,
   un par agent — et ne représente aucun cours.

3. **Lisible en vignette.** Peu de texte, fort contraste, rien sous la taille
   perceptible à cette échelle.
