"""Conversion récursive des types numpy vers des types Python natifs.

Les indicateurs calculent avec numpy (np.mean, comparaisons → np.bool_,
np.int64, np.float64...). Ces types ne sont pas sérialisables en JSON
par FastAPI ni par json.dumps → erreur "'numpy.bool' object is not
iterable" sur /api/agents. On nettoie donc toute structure destinée à
l'API ou à la base de données.
"""
import math
from typing import Any


def json_safe(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    # Types numpy (sans dépendre de l'import numpy si absent)
    try:
        import numpy as np
        if isinstance(o, np.generic):   # np.bool_, np.int64, np.float64...
            o = o.item()
        elif isinstance(o, np.ndarray):
            return [json_safe(v) for v in o.tolist()]
    except ImportError:
        pass
    # inf / NaN → None : ce sont des littéraux JSON INVALIDES ('Infinity',
    # 'NaN') que Starlette refuse (500) et que JSON.parse côté PWA rejette.
    # Cas fréquent : sortino_ratio = inf quand aucun rendement négatif.
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o
