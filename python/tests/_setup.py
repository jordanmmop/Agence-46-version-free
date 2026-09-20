"""Configuration commune des tests : ajoute python/ et la racine au path."""
import os
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
_PYTHON = os.path.dirname(_ICI)
_RACINE = os.path.dirname(_PYTHON)

for p in (_PYTHON, _RACINE):
    if p not in sys.path:
        sys.path.insert(0, p)

# Les tests fixent EUX-MÊMES les réglages qu'ils vérifient (voir
# trading_config.forcer_en_memoire) : sans ce point de départ neutre, la suite
# dépendrait du fichier de configuration de la machine qui la lance — un
# utilisateur ayant réglé son levier ou son risque par ordre dans l'interface
# verrait des tests échouer sans rapport avec son code.
try:
    from utils import trading_config as _tc

    _tc.forcer_en_memoire(
        risque_par_trade_pct=1.0,      # défaut historique des tests
        sizing_levier=False,           # volume = celui demandé, sans levier
        exiger_risque_calculable=True,
        autoriser_lot_minimum=False,
        cooldown_s=1800,
        sl_pct=2.0, tp_pct=3.0,
        spread_max_pct=0.5,
    )
except Exception:                       # suite lancée hors arborescence python/
    pass
