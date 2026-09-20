from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class StatutTrade(str, Enum):
    EN_ATTENTE = "PENDING"
    OUVERT = "OPEN"
    FERME = "CLOSED"
    ANNULE = "CANCELLED"
    REJETE = "REJECTED"


class DirectionTrade(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Trade:
    id: str
    symbole: str
    direction: DirectionTrade
    quantite: float
    prix_entree: float
    stop_loss: float
    take_profit: float
    statut: StatutTrade = StatutTrade.EN_ATTENTE
    prix_sortie: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    agent_source: str = ""
    ouvert_le: datetime = field(default_factory=datetime.now)
    ferme_le: Optional[datetime] = None
    notes: str = ""

    def fermer(self, prix_sortie: float) -> None:
        self.prix_sortie = prix_sortie
        self.statut = StatutTrade.FERME
        self.ferme_le = datetime.now()
        signe = 1 if self.direction == DirectionTrade.LONG else -1
        self.pnl = signe * (prix_sortie - self.prix_entree) * self.quantite - self.commission
        self.pnl_pct = (signe * (prix_sortie - self.prix_entree) / self.prix_entree * 100
                        if self.prix_entree else 0.0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbole": self.symbole,
            "direction": self.direction.value,
            "quantite": self.quantite,
            "prix_entree": self.prix_entree,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "statut": self.statut.value,
            "prix_sortie": self.prix_sortie,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "commission": round(self.commission, 2),
            "agent_source": self.agent_source,
            "ouvert_le": self.ouvert_le.isoformat(),
            "ferme_le": self.ferme_le.isoformat() if self.ferme_le else None,
        }
