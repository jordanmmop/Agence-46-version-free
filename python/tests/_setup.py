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

# ── Licence Pro DE TEST ────────────────────────────────────────────────────
# La suite historique exerce l'application COMPLÈTE : trading automatique,
# backtest, pré-vol, réglages avancés. Toutes ces fonctionnalités sont
# désormais réservées à l'abonnement Pro, et répondraient 402 en version
# d'essai — la suite mesurerait alors le verrou, plus le comportement qu'elle
# vérifie. Elle tourne donc sous une licence Pro ÉPHÉMÈRE, émise ici.
#
# Ce n'est PAS un contournement livré avec le produit :
#   - la paire de clés naît à chaque exécution et meurt avec le processus ;
#   - la clé publique n'est posée que dans l'environnement du test ;
#   - `agence.spec` exclut `python/tests/` du paquet distribué, donc ce code
#     n'existe sur aucune machine d'utilisateur.
#
# `test_licence.py` retire cette licence pour chacun de ses cas : c'est lui qui
# vérifie que la version d'essai est bien bridée.
def _licence_pro_de_test() -> None:
    import json
    import os
    import time

    if os.getenv("AGENCE_TESTS_SANS_LICENCE"):      # échappatoire de mise au point
        return
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from licence.verification import b64e
    except Exception:
        return                                       # suite lancée hors arborescence

    prive = Ed25519PrivateKey.generate()
    os.environ["AGENCE_LICENCE_PUBKEY"] = prive.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    charge = b64e(json.dumps({
        "sub": "suite-de-tests", "plan": "PRO", "iss": "agence46",
        "iat": int(time.time()), "exp": int(time.time() + 3600),
    }).encode())
    os.environ["AGENCE_LICENCE_JETON"] = \
        f"AGENCE1.{charge}.{b64e(prive.sign(charge.encode('ascii')))}"


try:
    _licence_pro_de_test()
except Exception:                                    # ne doit jamais bloquer la suite
    pass
