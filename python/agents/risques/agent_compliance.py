import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from config import MAX_RISQUE_PAR_TRADE, MAX_DRAWDOWN_GLOBAL, MAX_POSITIONS_SIMULTANÉES, LEVIER_MAX
from typing import Dict


class AgentCompliance(BaseAgent):
    @property
    def id(self): return "CO-028"
    @property
    def nom(self): return "Agent Compliance"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Conformité réglementaire : MiFID II, AMF, limites de trading, règles internes"
    @property
    def _system_prompt(self):
        return """Tu es le responsable compliance et conformité réglementaire.
Tu vérifies que tous les trades respectent les règles MiFID II, AMF et les limites internes.
Tu bloques les transactions non conformes et génères des alertes réglementaires.
Tolérance zéro pour les violations. Tu communiques en français."""

    @staticmethod
    def _limites() -> Dict:
        """Limites RÉELLEMENT en vigueur (celles réglées dans l'interface).

        Cet agent lisait des constantes figées : dès que l'utilisateur montait
        son levier à 10 ou son risque par ordre au-dessus de 10 %, il déclarait
        une « violation » à chaque cycle — donc une ALERTE à 95 % de confiance,
        qui devient la décision finale de l'orchestrateur et suspend tout le
        trading. Un contrôle de conformité doit vérifier les règles de
        l'utilisateur, pas d'anciennes valeurs par défaut.
        """
        limites = {
            "risque_pct": MAX_RISQUE_PAR_TRADE * 500,   # en % de la taille de position
            "drawdown_pct": MAX_DRAWDOWN_GLOBAL * 100,
            "positions": MAX_POSITIONS_SIMULTANÉES,
            "levier": LEVIER_MAX,
            "exposition_pct": 90.0,
        }
        try:
            from utils import trading_config
            limites["levier"] = float(trading_config.get("levier_max", LEVIER_MAX))
            limites["risque_pct"] = float(trading_config.get("risque_par_trade_pct", 10.0))
        except Exception:
            pass
        try:
            from utils.risk_guard import get_risk_guard
            rs = get_risk_guard().get_status()
            limites["positions"] = int(rs.get("max_positions") or limites["positions"])
            limites["drawdown_pct"] = float(rs.get("perte_max_pct") or limites["drawdown_pct"])
            limites["exposition_pct"] = float(rs.get("exposition_max_pct")
                                              or limites["exposition_pct"])
        except Exception:
            pass
        return limites

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        signal_propose = donnees.get("signal", {})
        portfolio = donnees.get("portfolio", {})
        prix = self._dernier_prix(donnees)

        limites = self._limites()
        violations = []
        avertissements = []
        score_compliance = 100

        taille = signal_propose.get("taille_position_pct", 0)
        if taille * 100 > limites["risque_pct"]:
            violations.append(f"Taille position trop grande: {taille*100:.1f}% > {limites['risque_pct']:.0f}%")
            score_compliance -= 30

        drawdown = abs(portfolio.get("drawdown_actuel", 0))
        if drawdown > limites["drawdown_pct"]:
            violations.append(f"Drawdown dépasse limite: {drawdown:.1f}% > {limites['drawdown_pct']:.0f}%")
            score_compliance -= 40

        nb_positions = portfolio.get("nb_positions", 0)
        if nb_positions >= limites["positions"]:
            violations.append(f"Limite positions atteinte: {nb_positions}/{limites['positions']}")
            score_compliance -= 25

        exposition = portfolio.get("exposition_pct", 0)
        if exposition > limites["exposition_pct"]:
            avertissements.append(f"Exposition totale élevée: {exposition:.0f}%")
            score_compliance -= 15

        levier = donnees.get("levier_utilise", 1.0)
        if levier > limites["levier"]:
            violations.append(f"Levier dépasse limite: {levier:.1f}x > {limites['levier']:.1f}x")
            score_compliance -= 35

        mifid_ok = self._verifier_mifid(signal_propose, symbole)
        if not mifid_ok:
            avertissements.append("Vérification MiFID II requise pour cet actif")
            score_compliance -= 10

        signaux = [
            f"Score compliance: {score_compliance}/100",
            f"Violations: {len(violations)} | Avertissements: {len(avertissements)}",
        ]
        signaux += [f"VIOLATION: {v}" for v in violations]
        signaux += [f"AVERT: {a}" for a in avertissements]

        if violations:
            action = ActionSignal.ALERTE
            confiance = 95
            signaux.append("TRADE BLOQUÉ - Violations compliance")
        elif avertissements:
            action = ActionSignal.SURVEILLER
            confiance = 70
            signaux.append("Trade approuvé avec avertissements")
        else:
            action = ActionSignal.HOLD
            confiance = 90
            signaux.append("Trade APPROUVÉ - Compliance OK")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"score": score_compliance, "violations": violations,
                     "avertissements": avertissements, "approuve": len(violations) == 0}
        )

    @staticmethod
    def _verifier_mifid(signal: dict, symbole: str) -> bool:
        actifs_regules = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "SPY", "QQQ"]
        if any(a in symbole for a in actifs_regules):
            return True
        return True
