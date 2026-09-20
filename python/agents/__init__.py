from .analyse_marche import (
    AgentAnalyseTechnique, AgentAnalyseFondamentale, AgentSentimentMarche,
    AgentTraderNews, AgentMacroEconomique, AgentSectoriel,
    AgentVolatilite, AgentCorrelation, AgentCycleMarche, AgentMomentum,
)
from .strategies import (
    AgentScalping, AgentDayTrading, AgentSwingTrading, AgentPositionTrading,
    AgentArbitrage, AgentMeanReversion, AgentBreakout, AgentGridTrading,
    AgentOptionsStrategie, AgentAlgoTrading,
)
from .risques import (
    AgentRiskManager, AgentStopLoss, AgentPositionSizing, AgentDiversification,
    AgentDrawdownControl, AgentGestionLiquidite, AgentStressTest, AgentCompliance,
)
from .execution import (
    AgentExecution, AgentOrderManagement, AgentMarketMaking, AgentSlippageControl,
    AgentAnalyseCouts, AgentSettlement, AgentReconciliation,
)
from .data_intelligence import (
    AgentCollecteurData, AgentMLPredicteur, AgentBacktesting,
    AgentPatternRecognition, AgentDonneesAlternatives, AgentRechercheQuant,
)
from .reporting import (
    AgentRapportPerformance, AgentSuiviPnL, AgentCommunicationClient, AgentReportingReglementaire,
)

AGENTS_ANALYSE_MARCHE = [
    AgentAnalyseTechnique(), AgentAnalyseFondamentale(), AgentSentimentMarche(),
    AgentTraderNews(), AgentMacroEconomique(), AgentSectoriel(),
    AgentVolatilite(), AgentCorrelation(), AgentCycleMarche(), AgentMomentum(),
]

AGENTS_STRATEGIES = [
    AgentScalping(), AgentDayTrading(), AgentSwingTrading(), AgentPositionTrading(),
    AgentArbitrage(), AgentMeanReversion(), AgentBreakout(), AgentGridTrading(),
    AgentOptionsStrategie(), AgentAlgoTrading(),
]

AGENTS_RISQUES = [
    AgentRiskManager(), AgentStopLoss(), AgentPositionSizing(), AgentDiversification(),
    AgentDrawdownControl(), AgentGestionLiquidite(), AgentStressTest(), AgentCompliance(),
]

AGENTS_EXECUTION = [
    AgentExecution(), AgentOrderManagement(), AgentMarketMaking(), AgentSlippageControl(),
    AgentAnalyseCouts(), AgentSettlement(), AgentReconciliation(),
]

AGENTS_DATA_INTELLIGENCE = [
    AgentCollecteurData(), AgentMLPredicteur(), AgentBacktesting(),
    AgentPatternRecognition(), AgentDonneesAlternatives(), AgentRechercheQuant(),
]

AGENTS_REPORTING = [
    AgentRapportPerformance(), AgentSuiviPnL(), AgentCommunicationClient(), AgentReportingReglementaire(),
]

TOUS_LES_AGENTS = (
    AGENTS_ANALYSE_MARCHE +
    AGENTS_STRATEGIES +
    AGENTS_RISQUES +
    AGENTS_EXECUTION +
    AGENTS_DATA_INTELLIGENCE +
    AGENTS_REPORTING
)

AGENTS_PAR_ID = {agent.id: agent for agent in TOUS_LES_AGENTS}
AGENTS_PAR_GROUPE = {
    "analyse_marche": AGENTS_ANALYSE_MARCHE,
    "strategies": AGENTS_STRATEGIES,
    "risques": AGENTS_RISQUES,
    "execution": AGENTS_EXECUTION,
    "data_intelligence": AGENTS_DATA_INTELLIGENCE,
    "reporting": AGENTS_REPORTING,
}

assert len(TOUS_LES_AGENTS) == 45, f"Attendu 45 agents, obtenu {len(TOUS_LES_AGENTS)}"
