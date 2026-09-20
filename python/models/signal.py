from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum

from .json_safe import json_safe


class ActionSignal(str, Enum):
    ACHAT = "BUY"
    VENTE = "SELL"
    HOLD = "HOLD"
    SURVEILLER = "WATCH"
    ALERTE = "ALERT"


@dataclass
class Signal:
    agent_id: str
    agent_nom: str
    symbole: str
    action: ActionSignal = ActionSignal.HOLD
    confiance: float = 0.0
    prix_entree: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    taille_position_pct: float = 0.0
    raisonnement: str = ""
    donnees: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        # json_safe : les agents stockent des valeurs numpy (np.bool_,
        # np.float64...) dans donnees — non sérialisables en JSON sinon.
        return json_safe({
            "agent_id": self.agent_id,
            "agent_nom": self.agent_nom,
            "symbole": self.symbole,
            "action": self.action.value if isinstance(self.action, ActionSignal) else self.action,
            "confiance": round(float(self.confiance), 2),
            "prix_entree": self.prix_entree,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "taille_position_pct": round(float(self.taille_position_pct), 4),
            "raisonnement": self.raisonnement,
            "donnees": self.donnees,
            "timestamp": self.timestamp.isoformat(),
        })

    @property
    def est_bullish(self) -> bool:
        return self.action == ActionSignal.ACHAT and self.confiance > 50

    @property
    def est_bearish(self) -> bool:
        return self.action == ActionSignal.VENTE and self.confiance > 50

    @property
    def ratio_risque_rendement(self) -> Optional[float]:
        if not all([self.prix_entree, self.stop_loss, self.take_profit]):
            return None
        risque = abs(self.prix_entree - self.stop_loss)
        rendement = abs(self.take_profit - self.prix_entree)
        return rendement / risque if risque > 0 else None
