import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np
from scipy import stats


class AgentRechercheQuant(BaseAgent):
    @property
    def id(self): return "QR-041"
    @property
    def nom(self): return "Chercheur Quantitatif"
    @property
    def groupe(self): return "data_intelligence"
    @property
    def description(self): return "Recherche quantitative : modèles mathématiques, tests statistiques, alpha"
    @property
    def _system_prompt(self):
        return """Tu es un chercheur quantitatif senior spécialisé en finance mathématique.
Tu développes des modèles statistiques et tests d'hypothèses sur les séries temporelles financières.
Tu identifies les anomalies de marché (calendar effects, momentum, reversion) et quantifies l'alpha.
Tu communiques en français avec précision mathématique."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        volumes = donnees.get("volumes", [])
        if len(closes) < 50:
            return self._signal_neutre(symbole, "Données insuffisantes pour recherche quant")

        prix = closes[-1]
        rendements = np.array([closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))])

        test_norm = stats.shapiro(rendements[-30:]) if len(rendements) >= 8 else (0, 0)
        est_normal = test_norm[1] > 0.05

        # Hurst DOIT porter sur les rendements : appliqué à la série de PRIX
        # (processus intégré), il ressort systématiquement autour de 0,9 et la
        # branche mean-reversion devenait inatteignable.
        hurst = self._exposant_hurst(rendements)
        tendance_hurst = "tendance" if hurst > 0.55 else "mean-reversion" if hurst < 0.45 else "marche aléatoire"

        autocorr_1 = float(np.corrcoef(rendements[:-1], rendements[1:])[0, 1]) if len(rendements) > 2 else 0
        autocorr_5 = float(np.corrcoef(rendements[:-5], rendements[5:])[0, 1]) if len(rendements) > 6 else 0

        kurtosis = float(stats.kurtosis(rendements))
        skewness = float(stats.skew(rendements))

        sharpe = Indicateurs.sharpe_ratio(list(rendements), 0.05)
        sortino = Indicateurs.sortino_ratio(list(rendements), 0.05)

        signaux = [
            f"Exposant Hurst: {hurst:.3f} ({tendance_hurst})",
            f"Distribution: {'Normale' if est_normal else 'Non-normale'} (kurtosis:{kurtosis:.2f}, skew:{skewness:.2f})",
            f"Autocorrélation lag-1: {autocorr_1:.3f} | lag-5: {autocorr_5:.3f}",
            f"Sharpe: {sharpe:.2f} | Sortino: {sortino:.2f}",
        ]

        if hurst > 0.6 and autocorr_1 > 0.1:
            action = ActionSignal.ACHAT if rendements[-1] > 0 else ActionSignal.VENTE
            confiance = min(75, 50 + (hurst - 0.5) * 100 + autocorr_1 * 50)
            sl = prix * 0.96
            tp = prix * 1.08
            signaux.append(f"Forte persistance (H={hurst:.2f}) → suivre tendance")
        elif hurst < 0.4 and abs(autocorr_1) > 0.1:
            action = ActionSignal.ACHAT if rendements[-1] < 0 else ActionSignal.VENTE
            confiance = min(70, 50 + (0.5 - hurst) * 100)
            sl = prix * 0.97
            tp = prix * 1.05
            signaux.append(f"Forte mean-reversion (H={hurst:.2f}) → contra-tendance")
        else:
            return self._signal_neutre(symbole, f"Processus proche marche aléatoire (H={hurst:.2f})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.04,
            donnees={"hurst": hurst, "autocorr_1": autocorr_1, "kurtosis": kurtosis,
                     "skewness": skewness, "sharpe": sharpe, "sortino": sortino}
        )

    @staticmethod
    def _exposant_hurst(serie: list) -> float:
        n = len(serie)
        if n < 20:
            return 0.5
        serie = np.array(serie)
        lags = [2, 4, 8, 16, min(32, n // 2)]
        rs_values = []
        for lag in lags:
            sub = [serie[i:i + lag] for i in range(0, n - lag, lag)]
            rs_lag = []
            for s in sub:
                mean_s = np.mean(s)
                deviation = np.cumsum(s - mean_s)
                R = max(deviation) - min(deviation)
                S = np.std(s)
                if S > 0:
                    rs_lag.append(R / S)
            if rs_lag:
                rs_values.append((np.log(lag), np.log(np.mean(rs_lag))))
        if len(rs_values) < 2:
            return 0.5
        x = [v[0] for v in rs_values]
        y = [v[1] for v in rs_values]
        coeffs = np.polyfit(x, y, 1)
        return float(np.clip(coeffs[0], 0, 1))
