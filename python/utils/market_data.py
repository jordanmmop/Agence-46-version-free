import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
import logging
from models.market_data import MarketData

logger = logging.getLogger(__name__)


class FetcheurDonnees:

    PERIODE_MAP = {
        "1m": "7d", "5m": "60d", "15m": "60d",
        "1h": "3mo", "4h": "6mo", "1d": "2y", "1wk": "5y",
    }
    INTERVAL_MAP = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "1h": "1h", "4h": "1h", "1d": "1d", "1wk": "1wk",
    }

    # Bougies MetaTrader 5 : équivalents des timeframes de l'application.
    MT5_TIMEFRAMES = {
        "1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15",
        "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1",
        "1wk": "TIMEFRAME_W1",
    }
    NB_BOUGIES_MT5 = 300

    @classmethod
    def depuis_mt5(cls, symbole: str, timeframe: str = "1h") -> Optional[MarketData]:
        """Bougies lues DIRECTEMENT dans le terminal MetaTrader 5 connecté.

        C'est la source à privilégier dès qu'un compte est connecté, pour deux
        raisons de fond :

        1. Ce sont les prix de l'instrument RÉELLEMENT tradé, chez le courtier
           où l'ordre partira — et non ceux d'un ticker Yahoo approchant.
        2. Yahoo (yfinance) tombe régulièrement : quota dépassé, 403, 429,
           changement d'API. L'application basculait alors sur des cours
           INVENTÉS, marqués « simulés », et le garde-fou « aucun ordre réel
           sur des prix fictifs » refusait ENSUITE chaque ordre. Résultat :
           plus un seul ordre ne partait, tant que Yahoo ne revenait pas —
           alors même que le courtier, lui, diffusait ses prix normalement.

        Retourne None si aucun compte n'est connecté ou si le symbole n'existe
        pas chez le courtier : l'appelant retombe alors sur yfinance.
        """
        try:
            from utils.mt5_manager import get_mt5_manager
            gest = get_mt5_manager()
            if not (getattr(gest, "_lib_available", False) and gest.get_status().get("connected")):
                return None
            import MetaTrader5 as mt5
            tf = getattr(mt5, cls.MT5_TIMEFRAMES.get(timeframe, "TIMEFRAME_H1"), None)
            if tf is None:
                return None
            with gest.verrou:                     # connexion terminal non thread-safe
                sym = gest.resoudre_symbole(symbole)
                if not sym:
                    return None
                rates = mt5.copy_rates_from_pos(sym, tf, 0, cls.NB_BOUGIES_MT5)
            if rates is None or len(rates) == 0:
                return None
            df = pd.DataFrame(rates)
            if df.empty or "close" not in df:
                return None
            df.index = pd.to_datetime(df["time"], unit="s")
            df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                    "close": "Close", "tick_volume": "Volume"})
            md = MarketData.depuis_dataframe(symbole, timeframe,
                                             df[["Open", "High", "Low", "Close", "Volume"]])
            if not md.bougies:
                return None
            md.indicateurs["source"] = "MT5"
            logger.info(f"Cours {symbole} lus chez le courtier ({sym}, "
                        f"{len(md.bougies)} bougies)")
            return md
        except Exception as e:
            logger.warning(f"Cours MetaTrader indisponibles pour {symbole} : {e}")
            return None

    @classmethod
    def obtenir_donnees(cls, symbole: str, timeframe: str = "1h", periode: str = None,
                        prefer_courtier: bool = True) -> Optional[MarketData]:
        # 1. Le COURTIER d'abord quand un compte est connecté : ce sont les
        #    prix sur lesquels l'ordre sera exécuté, et ils ne dépendent
        #    d'aucun quota externe.
        #    `prefer_courtier=False` pour les usages qui ont besoin d'un
        #    historique PROFOND (backtest) : le terminal n'en garde que les
        #    dernières bougies, là où Yahoo remonte sur plusieurs années.
        if prefer_courtier:
            md_courtier = cls.depuis_mt5(symbole, timeframe)
            if md_courtier is not None:
                return md_courtier
        # 2. Sinon Yahoo (mode simulation, backtests, machine sans MetaTrader).
        try:
            interval = cls.INTERVAL_MAP.get(timeframe, "1h")
            period = periode or cls.PERIODE_MAP.get(timeframe, "3mo")
            ticker = yf.Ticker(symbole)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                logger.warning(f"Pas de données pour {symbole}")
                return cls._donnees_simulees(symbole, timeframe)
            df.index = pd.to_datetime(df.index)
            if timeframe == "4h":
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max",
                    "Low": "min", "Close": "last", "Volume": "sum",
                }).dropna()
                if df.empty:
                    # Le fetch réel a réussi mais le ré-échantillonnage n'a rien
                    # laissé : sans ce garde on renverrait des données « réelles »
                    # vides, sans trace de la dégradation.
                    logger.warning(f"Ré-échantillonnage 4h vide pour {symbole}")
                    return cls._donnees_simulees(symbole, timeframe)
            return MarketData.depuis_dataframe(symbole, timeframe, df)
        except Exception as e:
            logger.error(f"Erreur fetch {symbole}: {e}")
            return cls._donnees_simulees(symbole, timeframe)

    @classmethod
    def obtenir_multi(cls, symboles: List[str], timeframe: str = "1h") -> Dict[str, MarketData]:
        # walrus : un seul appel réseau par symbole (au lieu de deux)
        return {s: md for s in symboles if (md := cls.obtenir_donnees(s, timeframe))}

    @classmethod
    def _donnees_simulees(cls, symbole: str, timeframe: str = "1h") -> MarketData:
        import zlib
        # Générateur LOCAL : np.random.seed() muterait l'état global partagé
        # entre les threads (agents en ThreadPoolExecutor + requêtes API).
        # crc32 : graine stable entre exécutions (hash() est salé par processus).
        rng = np.random.default_rng(zlib.crc32(symbole.encode()))
        n = 200
        prix_base = {"BTC-USD": 45000, "ETH-USD": 2500, "AAPL": 180, "SPY": 500}.get(symbole, 100)
        rendements = rng.normal(0.0001, 0.02, n)
        closes = [prix_base]
        for r in rendements:
            closes.append(closes[-1] * (1 + r))
        closes = closes[1:]
        df_data = []
        now = datetime.now()
        for c in closes:
            spread = c * 0.002
            o = c * (1 + rng.uniform(-0.005, 0.005))
            h = max(o, c) + rng.uniform(0, spread)
            l = min(o, c) - rng.uniform(0, spread)
            df_data.append({"Open": o, "High": h, "Low": l, "Close": c,
                            "Volume": rng.uniform(1e6, 1e8)})
        df = pd.DataFrame(df_data, index=pd.date_range(
            end=now, periods=n, freq="h" if timeframe == "1h" else "D"
        ))
        md = MarketData.depuis_dataframe(symbole, timeframe, df)
        md.indicateurs["simule"] = True
        return md

    @classmethod
    def obtenir_info_ticker(cls, symbole: str) -> dict:
        try:
            ticker = yf.Ticker(symbole)
            info = ticker.info
            return {
                "nom": info.get("longName", symbole),
                "secteur": info.get("sector", "N/A"),
                "industrie": info.get("industry", "N/A"),
                "capitalisation": info.get("marketCap", 0),
                "pe_ratio": info.get("trailingPE", None),
                "eps": info.get("trailingEps", None),
                "dividende_yield": info.get("dividendYield", None),
                "beta": info.get("beta", None),
                "52w_haut": info.get("fiftyTwoWeekHigh", None),
                "52w_bas": info.get("fiftyTwoWeekLow", None),
            }
        except Exception as e:
            logger.error(f"Erreur info {symbole}: {e}")
            return {"nom": symbole, "secteur": "N/A"}

    @classmethod
    def obtenir_correlations(cls, symboles: List[str], periode: str = "3mo") -> pd.DataFrame:
        try:
            closes = {}
            for s in symboles:
                ticker = yf.Ticker(s)
                df = ticker.history(period=periode, interval="1d")
                if not df.empty:
                    closes[s] = df["Close"]
            if not closes:
                return pd.DataFrame()
            df_closes = pd.DataFrame(closes).dropna()
            return df_closes.pct_change().corr()
        except Exception as e:
            logger.error(f"Erreur corrélations: {e}")
            return pd.DataFrame()
