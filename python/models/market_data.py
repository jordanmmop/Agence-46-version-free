from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
import math
import pandas as pd


@dataclass
class OHLCV:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def corps(self) -> float:
        return abs(self.close - self.open)

    @property
    def est_haussier(self) -> bool:
        return self.close > self.open


@dataclass
class MarketData:
    symbole: str
    timeframe: str
    bougies: List[OHLCV] = field(default_factory=list)
    prix_actuel: float = 0.0
    volume_24h: float = 0.0
    variation_24h: float = 0.0
    capitalisation: float = 0.0
    derniere_maj: datetime = field(default_factory=datetime.now)
    indicateurs: Dict = field(default_factory=dict)

    @classmethod
    def depuis_dataframe(cls, symbole: str, timeframe: str, df: pd.DataFrame) -> "MarketData":
        bougies = []
        for idx, row in df.iterrows():
            o = float(row.get("Open", row.get("open", 0)))
            h = float(row.get("High", row.get("high", 0)))
            b = float(row.get("Low", row.get("low", 0)))
            c = float(row.get("Close", row.get("close", 0)))
            v = float(row.get("Volume", row.get("volume", 0)))
            # Bougie INEXPLOITABLE (NaN / infini) → écartée.
            # yfinance renvoie des NaN sur les barres manquantes (jour férié,
            # trou de flux, instrument suspendu). Sans ce filtre, un NaN en
            # dernière position donnait `prix_actuel = nan`, et le garde-fou
            # « pas d'ordre réel sur des données inutilisables » ne se
            # déclenchait PAS : `nan` est vrai au sens booléen, donc
            # `not float(prix or 0)` valait False. Un stop-loss et un
            # take-profit NaN partaient alors au courtier.
            if not all(math.isfinite(x) for x in (o, h, b, c)):
                continue
            if not math.isfinite(v):
                v = 0.0
            bougies.append(OHLCV(
                timestamp=idx if isinstance(idx, datetime) else datetime.fromisoformat(str(idx)),
                open=o, high=h, low=b, close=c, volume=v,
            ))
        prix_actuel = bougies[-1].close if bougies else 0.0
        variation = 0.0
        if len(bougies) > 1 and bougies[-2].close != 0:
            variation = (bougies[-1].close - bougies[-2].close) / bougies[-2].close * 100
        return cls(
            symbole=symbole,
            timeframe=timeframe,
            bougies=bougies,
            prix_actuel=prix_actuel,
            variation_24h=variation,
        )

    @property
    def closes(self) -> List[float]:
        return [b.close for b in self.bougies]

    @property
    def highs(self) -> List[float]:
        return [b.high for b in self.bougies]

    @property
    def lows(self) -> List[float]:
        return [b.low for b in self.bougies]

    @property
    def volumes(self) -> List[float]:
        return [b.volume for b in self.bougies]

    def to_dict(self) -> dict:
        from .json_safe import json_safe
        # json_safe : les indicateurs contiennent des valeurs numpy
        return json_safe({
            "symbole": self.symbole,
            "timeframe": self.timeframe,
            "prix_actuel": round(float(self.prix_actuel), 4),
            "volume_24h": self.volume_24h,
            "variation_24h": round(float(self.variation_24h), 2),
            "nb_bougies": len(self.bougies),
            "derniere_maj": self.derniere_maj.isoformat(),
            "indicateurs": self.indicateurs,
        })
