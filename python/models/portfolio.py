from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
from .trade import Trade, StatutTrade, DirectionTrade


@dataclass
class Position:
    symbole: str
    quantite: float
    prix_moyen: float
    valeur_marche: float = 0.0
    pnl_latent: float = 0.0
    pnl_pct: float = 0.0
    # Sens de la position : sans lui, le P&L latent d'un SHORT était inversé
    # (gain affiché quand le prix monte). Même convention de signe que Trade.
    direction: DirectionTrade = DirectionTrade.LONG

    def mettre_a_jour(self, prix_actuel: float) -> None:
        signe = 1 if self.direction == DirectionTrade.LONG else -1
        self.valeur_marche = self.quantite * prix_actuel
        self.pnl_latent = signe * (prix_actuel - self.prix_moyen) * self.quantite
        self.pnl_pct = (signe * (prix_actuel - self.prix_moyen) / self.prix_moyen * 100
                        if self.prix_moyen else 0.0)

    def to_dict(self) -> dict:
        return {
            "symbole": self.symbole,
            "quantite": self.quantite,
            "prix_moyen": round(self.prix_moyen, 4),
            "direction": self.direction.value if isinstance(self.direction, DirectionTrade) else self.direction,
            "valeur_marche": round(self.valeur_marche, 2),
            "pnl_latent": round(self.pnl_latent, 2),
            "pnl_pct": round(self.pnl_pct, 2),
        }


@dataclass
class Portfolio:
    capital_initial: float
    capital_disponible: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    historique_valeur: List[Dict] = field(default_factory=list)
    cree_le: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if self.capital_disponible == 0.0:
            self.capital_disponible = self.capital_initial

    @property
    def valeur_positions(self) -> float:
        return sum(p.valeur_marche for p in self.positions.values())

    @property
    def valeur_totale(self) -> float:
        return self.capital_disponible + self.valeur_positions

    @property
    def pnl_total(self) -> float:
        return self.valeur_totale - self.capital_initial

    @property
    def pnl_total_pct(self) -> float:
        return self.pnl_total / self.capital_initial * 100 if self.capital_initial else 0.0

    @property
    def pnl_realise(self) -> float:
        return sum(t.pnl for t in self.trades if t.statut == StatutTrade.FERME)

    @property
    def drawdown_actuel(self) -> float:
        if not self.historique_valeur:
            return 0.0
        valeurs = [h["valeur"] for h in self.historique_valeur]
        pic = max(valeurs)
        actuel = valeurs[-1]
        return (actuel - pic) / pic * 100 if pic else 0.0

    @property
    def exposition_pct(self) -> float:
        return self.valeur_positions / self.valeur_totale * 100 if self.valeur_totale > 0 else 0.0

    def enregistrer_valeur(self) -> None:
        self.historique_valeur.append({
            "timestamp": datetime.now().isoformat(),
            "valeur": self.valeur_totale,
            "pnl": self.pnl_total,
        })

    def to_dict(self) -> dict:
        return {
            "capital_initial": self.capital_initial,
            "capital_disponible": round(self.capital_disponible, 2),
            "valeur_positions": round(self.valeur_positions, 2),
            "valeur_totale": round(self.valeur_totale, 2),
            "pnl_total": round(self.pnl_total, 2),
            "pnl_total_pct": round(self.pnl_total_pct, 2),
            "pnl_realise": round(self.pnl_realise, 2),
            "drawdown_actuel": round(self.drawdown_actuel, 2),
            "exposition_pct": round(self.exposition_pct, 2),
            "nb_positions": len(self.positions),
            "nb_trades": len(self.trades),
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
        }
