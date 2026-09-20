import numpy as np
from typing import List, Tuple


class Indicateurs:

    @staticmethod
    def sma(prix: List[float], periode: int) -> List[float]:
        # Longueur garantie == len(prix) (padding None en tête), même si
        # l'entrée est plus courte que la période.
        result = [None] * len(prix)
        for i in range(periode - 1, len(prix)):
            result[i] = np.mean(prix[i - periode + 1: i + 1])
        return result

    @staticmethod
    def ema(prix: List[float], periode: int) -> List[float]:
        if len(prix) < periode:
            return [None] * len(prix)
        k = 2.0 / (periode + 1)
        result = [None] * (periode - 1)
        result.append(np.mean(prix[:periode]))
        for i in range(periode, len(prix)):
            result.append(prix[i] * k + result[-1] * (1 - k))
        return result

    @staticmethod
    def rsi(prix: List[float], periode: int = 14) -> List[float]:
        if len(prix) < periode + 1:
            return [50.0] * len(prix)
        deltas = [prix[i] - prix[i - 1] for i in range(1, len(prix))]
        result = [None] * periode
        gains = [max(0, d) for d in deltas[:periode]]
        pertes = [abs(min(0, d)) for d in deltas[:periode]]
        avg_gain = np.mean(gains)
        avg_perte = np.mean(pertes)
        # avg_perte==0 : 100 seulement s'il y a des hausses ; prix plats → 50
        if avg_perte == 0:
            result.append(100.0 if avg_gain > 0 else 50.0)
        else:
            rs = avg_gain / avg_perte
            result.append(100 - 100 / (1 + rs))
        for i in range(periode, len(deltas)):
            gain = max(0, deltas[i])
            perte = abs(min(0, deltas[i]))
            avg_gain = (avg_gain * (periode - 1) + gain) / periode
            avg_perte = (avg_perte * (periode - 1) + perte) / periode
            if avg_perte == 0:
                result.append(100.0 if avg_gain > 0 else 50.0)
            else:
                rs = avg_gain / avg_perte
                result.append(100 - 100 / (1 + rs))
        return result

    @staticmethod
    def macd(prix: List[float], rapide: int = 12, lent: int = 26, signal: int = 9) -> Tuple[List, List, List]:
        ema_rapide = Indicateurs.ema(prix, rapide)
        ema_lente = Indicateurs.ema(prix, lent)
        macd_line = []
        for r, l in zip(ema_rapide, ema_lente):
            if r is None or l is None:
                macd_line.append(None)
            else:
                macd_line.append(r - l)
        valides = [v for v in macd_line if v is not None]
        if len(valides) < signal:
            # pas assez de points pour la ligne de signal → tout None,
            # longueur garantie == macd_line
            signal_line = [None] * len(macd_line)
        else:
            signal_line_valides = Indicateurs.ema(valides, signal)
            signal_line = [None] * (len(macd_line) - len(valides))
            signal_line += [None] * (signal - 1) + signal_line_valides[signal - 1:]
        histogramme = []
        for m, s in zip(macd_line, signal_line):
            if m is None or s is None:
                histogramme.append(None)
            else:
                histogramme.append(m - s)
        return macd_line, signal_line, histogramme

    @staticmethod
    def bollinger(prix: List[float], periode: int = 20, nb_ecart: float = 2.0) -> Tuple[List, List, List]:
        sma = Indicateurs.sma(prix, periode)
        upper, lower = [], []
        for i in range(len(prix)):
            if sma[i] is None:
                upper.append(None)
                lower.append(None)
            else:
                std = np.std(prix[max(0, i - periode + 1): i + 1])
                upper.append(sma[i] + nb_ecart * std)
                lower.append(sma[i] - nb_ecart * std)
        return upper, sma, lower

    @staticmethod
    def atr(highs: List[float], lows: List[float], closes: List[float], periode: int = 14) -> List[float]:
        n = len(closes)
        if n < 2:
            return [None] * n
        tr_list = [highs[0] - lows[0]]
        for i in range(1, n):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)
        # Longueur garantie == len(closes) ; ATR calculé seulement si assez
        # de True Ranges pour la période (sinon padding None).
        result = [None] * n
        if len(tr_list) >= periode:
            atr_val = np.mean(tr_list[:periode])
            result[periode - 1] = atr_val
            for i in range(periode, len(tr_list)):
                atr_val = (atr_val * (periode - 1) + tr_list[i]) / periode
                result[i] = atr_val
        return result

    @staticmethod
    def stochastique(highs: List[float], lows: List[float], closes: List[float], k: int = 14, d: int = 3) -> Tuple[List, List]:
        k_line = []
        for i in range(len(closes)):
            if i < k - 1:
                k_line.append(None)
            else:
                h = max(highs[i - k + 1: i + 1])
                l = min(lows[i - k + 1: i + 1])
                k_line.append(((closes[i] - l) / (h - l) * 100) if h != l else 50.0)
        valides = [v for v in k_line if v is not None]
        if len(valides) < d:
            d_line = [None] * len(k_line)
        else:
            d_valides = Indicateurs.sma(valides, d)
            d_line = [None] * (len(k_line) - len(valides)) + d_valides
        return k_line, d_line

    @staticmethod
    def obv(closes: List[float], volumes: List[float]) -> List[float]:
        """On-Balance Volume. Longueur garantie == len(closes).

        Robuste aux volumes ABSENTS ou plus courts que les clôtures : yfinance
        n'en renvoie pas toujours, et l'orchestrateur passe `volumes: []` quand
        les données de marché sont indisponibles. Avant, `volumes[0]` levait
        IndexError et l'agent retombait silencieusement en signal neutre.
        """
        n = len(closes)
        if n == 0:
            return []
        vol = list(volumes or [])
        if len(vol) < n:                     # complété par 0 : volume inconnu
            vol = vol + [0.0] * (n - len(vol))
        obv = [vol[0] or 0.0]
        for i in range(1, n):
            v = vol[i] or 0.0
            if closes[i] > closes[i - 1]:
                obv.append(obv[-1] + v)
            elif closes[i] < closes[i - 1]:
                obv.append(obv[-1] - v)
            else:
                obv.append(obv[-1])
        return obv

    @staticmethod
    def fibonacci_retracement(haut: float, bas: float) -> dict:
        diff = haut - bas
        return {
            "0.0": haut,
            "0.236": haut - 0.236 * diff,
            "0.382": haut - 0.382 * diff,
            "0.5": haut - 0.5 * diff,
            "0.618": haut - 0.618 * diff,
            "0.786": haut - 0.786 * diff,
            "1.0": bas,
        }

    # 252 jours de bourse × ~6,5 h de séance = 1638 bougies 1h par an.
    # TOUT le pipeline alimente les agents en bougies 1h (orchestrateur
    # _preparer_donnees → obtenir_donnees(symbole, "1h")), jamais en
    # quotidien : annualiser avec sqrt(252) divisait la volatilité mesurée
    # par ~2,5 et rendait inatteignables les seuils d'alerte des agents.
    BOUGIES_PAR_AN_1H = 252 * 6.5

    @staticmethod
    def volatilite_historique(closes: List[float], periode: int = 20,
                              bougies_par_an: float = 252 * 6.5) -> float:
        """Volatilité annualisée en %.

        `bougies_par_an` DOIT correspondre au pas de temps des `closes` :
        252 pour du quotidien, 252×6,5 = 1638 pour des bougies 1h d'actions
        ou d'indices, 252×24 = 6048 pour du 24/7 (crypto, forex).
        """
        if len(closes) < 2:
            return 0.0
        # Les clôtures NULLES ou négatives sont ignorées : `closes[i]/closes[i-1]`
        # levait ZeroDivisionError (et log(x≤0) donne nan/-inf). Un zéro se
        # glisse dans un flux de cours (ligne incomplète, instrument suspendu),
        # et l'exception remontait jusqu'à `BaseAgent.analyser` : les SEPT
        # agents qui appellent cette fonction (volatilité, sentiment, options,
        # grille, stop-loss, market making) retombaient alors en signal neutre
        # « Erreur », sans que la cause soit visible nulle part.
        rendements = [np.log(closes[i] / closes[i - 1])
                      for i in range(1, len(closes))
                      if closes[i] > 0 and closes[i - 1] > 0]
        if not rendements:
            return 0.0
        echantillon = rendements if len(rendements) < periode else rendements[-periode:]
        return float(np.std(echantillon) * np.sqrt(bougies_par_an) * 100)

    @staticmethod
    def vwap(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> List[float]:
        """VWAP cumulé. Longueur garantie == len(closes).

        Un `zip` sur des volumes plus courts TRONQUAIT silencieusement la
        sortie : les appelants lisent `vwap[-1]` et obtenaient soit un point
        périmé, soit un IndexError sur liste vide (volumes absents).
        """
        n = len(closes)
        if n == 0:
            return []
        vol = list(volumes or [])
        if len(vol) < n:
            vol = vol + [0.0] * (n - len(vol))
        result = []
        cum_vol = 0.0
        cum_tp_vol = 0.0
        for i in range(n):
            c = closes[i]
            h = highs[i] if i < len(highs) else c
            l = lows[i] if i < len(lows) else c
            v = vol[i] or 0.0
            tp = (h + l + c) / 3
            cum_tp_vol += tp * v
            cum_vol += v
            result.append(cum_tp_vol / cum_vol if cum_vol > 0 else c)
        return result

    @staticmethod
    def support_resistance(highs: List[float], lows: List[float], fenetre: int = 5) -> Tuple[List[float], List[float]]:
        supports, resistances = [], []
        for i in range(fenetre, len(lows) - fenetre):
            if lows[i] == min(lows[i - fenetre: i + fenetre + 1]):
                supports.append(lows[i])
        for i in range(fenetre, len(highs) - fenetre):
            if highs[i] == max(highs[i - fenetre: i + fenetre + 1]):
                resistances.append(highs[i])
        return sorted(supports), sorted(resistances)

    @staticmethod
    def z_score(prix: List[float], periode: int = 20) -> List[float]:
        # Contrat de longueur (comme sma/atr) : la sortie fait TOUJOURS len(prix).
        # Un `[None]*(periode-1)` + append donnerait une liste plus LONGUE que
        # l'entrée quand len(prix) < periode (désalignement des indices).
        result = [None] * len(prix)
        for i in range(periode - 1, len(prix)):
            segment = prix[i - periode + 1: i + 1]
            mu = np.mean(segment)
            sigma = np.std(segment)
            result[i] = (prix[i] - mu) / sigma if sigma > 0 else 0.0
        return result

    @staticmethod
    def kelly_criterion(win_rate: float, avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 0.0
        b = avg_gain / avg_loss
        if b <= 0:      # avg_gain == 0 → division par b plus bas
            return 0.0
        f = (b * win_rate - (1 - win_rate)) / b
        return max(0.0, min(f, 0.25))

    @staticmethod
    def sharpe_ratio(rendements: List[float], taux_rf: float = 0.05) -> float:
        if len(rendements) < 2:
            return 0.0
        r_annualise = np.mean(rendements) * 252
        vol = np.std(rendements) * np.sqrt(252)
        return (r_annualise - taux_rf) / vol if vol > 0 else 0.0

    # Valeur FINIE représentant « aucun risque de baisse mesuré » — même
    # convention que le profit_factor plafonné à 999 dans agent_performance.
    # Renvoyer float("inf") obligeait chaque appelant à le traiter à part :
    # aucun ne le faisait, et le tableau de bord affichait « Sortino: inf ».
    SORTINO_MAX = 999.0

    @staticmethod
    def sortino_ratio(rendements: List[float], taux_rf: float = 0.05) -> float:
        if len(rendements) < 2:
            return 0.0
        negatifs = [r for r in rendements if r < 0]
        r_annualise = float(np.mean(rendements)) * 252
        if not negatifs:
            # Aucun rendement négatif : pas de risque de baisse à diviser.
            # Le signe suit la performance (un rendement nul n'est pas un
            # Sortino excellent).
            if r_annualise - taux_rf <= 0:
                return 0.0
            return Indicateurs.SORTINO_MAX
        downside = float(np.std(negatifs)) * np.sqrt(252)
        if downside <= 0:
            return 0.0
        return max(-Indicateurs.SORTINO_MAX,
                   min(Indicateurs.SORTINO_MAX, (r_annualise - taux_rf) / downside))

    @staticmethod
    def max_drawdown(valeurs: List[float]) -> float:
        if len(valeurs) < 2:
            return 0.0
        pic = valeurs[0]
        max_dd = 0.0
        for v in valeurs:
            if v > pic:
                pic = v
            if not pic:          # pic nul (capital initial à 0) → pas de ratio
                continue         #   sans ce garde : ZeroDivisionError
            dd = (v - pic) / pic * 100
            if dd < max_dd:
                max_dd = dd
        return abs(max_dd)
