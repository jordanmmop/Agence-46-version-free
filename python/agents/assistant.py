"""Assistants IA locaux — un par agent, pour les aider dans leurs tâches.

Les deux premières étapes sont 100 % locales (règles et statistiques) ;
seule la troisième consulte le moteur IA choisi :
1. AVANT l'analyse : contrôle la qualité des données et prépare un contexte
   enrichi (tendance, volatilité, extrêmes récents) pour son agent.
2. APRÈS l'analyse : vérifie la cohérence du signal (stop-loss / take-profit,
   ratio risque/rendement, calibrage de la confiance) et le corrige si besoin.
3. Pour les signaux à forte conviction : demande un deuxième avis au moteur
   IA sélectionné — Ollama ou Hermès, tous deux SUR LA MACHINE — (throttlé
   globalement pour ne pas ralentir les cycles).
"""
import logging
import os
import threading
import time
from statistics import pstdev
from typing import Any, Dict, Optional

from models.signal import Signal, ActionSignal

logger = logging.getLogger(__name__)

# ── Throttle global des consultations LLM ────────────────────────────
# 46 assistants partagent le même moteur IA : on espace les appels
# pour que les cycles d'analyse restent rapides.
_llm_lock = threading.Lock()
_derniere_consultation = 0.0
LLM_INTERVALLE_MIN = float(os.getenv("ASSISTANT_LLM_INTERVALLE", "8"))
LLM_CONFIANCE_MIN  = float(os.getenv("ASSISTANT_LLM_CONFIANCE", "70"))


def _peut_consulter_llm() -> bool:
    """Réserve un créneau de consultation LLM (non bloquant)."""
    global _derniere_consultation
    with _llm_lock:
        now = time.time()
        if now - _derniere_consultation >= LLM_INTERVALLE_MIN:
            _derniere_consultation = now
            return True
        return False


class AssistantIA:
    """Assistant local attaché à un agent (46 assistants pour 46 agents)."""

    def __init__(self, agent_id: str, agent_nom: str, groupe: str):
        self.id = f"AST-{agent_id}"
        self.nom = f"Assistant {agent_nom}"
        self.groupe = groupe
        self.statut = "idle"
        self.nb_preparations = 0
        self.nb_verifications = 0
        self.nb_corrections = 0
        self.nb_avis_llm = 0
        self.dernier_avis: Optional[str] = None
        self.derniere_qualite: Optional[str] = None

    # ── 1. Préparation du contexte (avant analyse) ────────────────────
    def preparer_contexte(self, symbole: str, donnees: Dict, contexte: Dict) -> Dict:
        self.statut = "preparation"
        self.nb_preparations += 1
        try:
            closes = [c for c in (donnees.get("closes") or []) if c is not None]

            if len(closes) >= 50:
                qualite = "bonne"
            elif len(closes) >= 10:
                qualite = "partielle"
            else:
                qualite = "insuffisante"
            self.derniere_qualite = qualite

            resume: Dict[str, Any] = {"qualite_donnees": qualite, "nb_points": len(closes)}

            if len(closes) >= 20:
                recent  = closes[-10:]
                anciens = closes[-30:-10] if len(closes) >= 30 else closes[:-10]
                moy_recent  = sum(recent) / len(recent)
                moy_ancien  = sum(anciens) / len(anciens)
                if moy_ancien:
                    ecart_pct = (moy_recent - moy_ancien) / moy_ancien * 100
                    resume["tendance"] = (
                        "haussiere" if ecart_pct > 1 else
                        "baissiere" if ecart_pct < -1 else "neutre"
                    )
                    resume["tendance_pct"] = round(ecart_pct, 2)

                fenetre = closes[-20:]
                moy = sum(fenetre) / len(fenetre)
                if moy:
                    resume["volatilite_pct"] = round(pstdev(fenetre) / moy * 100, 2)
                resume["plus_haut_20"] = round(max(fenetre), 6)
                resume["plus_bas_20"]  = round(min(fenetre), 6)

            contexte = dict(contexte or {})
            contexte["assistant"] = resume
        except Exception as e:
            logger.debug(f"[{self.nom}] Préparation contexte : {e}")
        finally:
            self.statut = "idle"
        return contexte

    # ── 2. Vérification du signal (après analyse) ─────────────────────
    def verifier_signal(self, signal: Signal, donnees: Dict) -> Signal:
        self.statut = "verification"
        self.nb_verifications += 1
        notes = []
        try:
            action = signal.action
            prix, sl, tp = signal.prix_entree, signal.stop_loss, signal.take_profit

            # Stop-loss ou take-profit NÉGATIF OU NUL. Plusieurs agents
            # calculent leurs niveaux par soustraction d'un multiple de l'ATR
            # ou d'une amplitude récente :
            #     tp = prix - (haut_zone - prix) * 1.5   (breakout baissier)
            #     tp = prix - (sl - prix) * 2.5          (momentum baissier)
            #     sl = prix * (1 - atr / prix * 1.5)     (mean reversion)
            #     tp = prediction                        (extrapolation ML)
            # Sur un actif qui décroche fortement (ATR de 25 % du prix, mèche
            # de 40 % sur cinq bougies, pente extrapolée sur un krach), ces
            # formules passent SOUS ZÉRO. Aucun contrôle en aval ne le voyait :
            # le test « du bon côté du prix » est satisfait par un nombre
            # négatif pour une VENTE, et le courtier recevait un prix
            # impossible. Un niveau ≤ 0 n'a aucun sens — on l'annule et on
            # laisse le filet SL/TP par défaut le remplacer.
            if action in (ActionSignal.ACHAT, ActionSignal.VENTE):
                if sl is not None and sl <= 0:
                    signal.stop_loss, sl = None, None
                    self.nb_corrections += 1
                    notes.append("Stop-loss négatif ou nul — annulé")
                if tp is not None and tp <= 0:
                    signal.take_profit, tp = None, None
                    self.nb_corrections += 1
                    notes.append("Take-profit négatif ou nul — annulé")

            # Ordre DÉGÉNÉRÉ : stop-loss ou take-profit confondu avec le prix
            # d'entrée (ou l'un avec l'autre). Un tel ordre serait rejeté par le
            # courtier, ou pire exécuté avec une protection nulle. Dernier
            # rempart valable pour TOUS les agents, quel que soit leur calcul.
            if action in (ActionSignal.ACHAT, ActionSignal.VENTE) and prix:
                ecart_min = abs(prix) * 0.001          # 0,1 % du prix
                degenere = ((sl is not None and abs(prix - sl) < ecart_min)
                            or (tp is not None and abs(tp - prix) < ecart_min)
                            or (sl is not None and tp is not None
                                and abs(tp - sl) < ecart_min))
                if degenere:
                    signal.stop_loss = None
                    signal.take_profit = None
                    signal.confiance = min(signal.confiance, 40.0)
                    self.nb_corrections += 1
                    notes.append("SL/TP dégénérés (confondus avec le prix) — "
                                 "annulés, confiance plafonnée à 40")
                    prix, sl, tp = signal.prix_entree, None, None

            # SL/TP inversés → correction automatique
            if action in (ActionSignal.ACHAT, ActionSignal.VENTE) and prix and sl and tp:
                sl_tp_inverses = (
                    (action == ActionSignal.ACHAT and (sl > prix or tp < prix)) or
                    (action == ActionSignal.VENTE and (sl < prix or tp > prix))
                )
                if sl_tp_inverses and ((sl > prix) != (tp > prix)):
                    signal.stop_loss, signal.take_profit = tp, sl
                    self.nb_corrections += 1
                    notes.append("SL/TP inversés — corrigés")
                elif sl_tp_inverses:
                    # SL et TP du MÊME côté du prix : non réparable par échange.
                    # On les ANNULE au lieu de les conserver. Les garder n'avait
                    # que des inconvénients : un stop-loss au-dessus du prix
                    # d'entrée sur un ACHAT est une perte immédiate, et surtout
                    # l'orchestrateur MOYENNE les SL/TP des agents qui votent
                    # dans le même sens — une valeur incohérente contaminait
                    # donc le niveau réellement envoyé au courtier. Annulés, ils
                    # laissent le filet SL/TP par défaut poser des niveaux sains.
                    signal.stop_loss = None
                    signal.take_profit = None
                    signal.confiance = max(0.0, signal.confiance - 10)
                    self.nb_corrections += 1
                    notes.append("SL/TP incohérents (même côté du prix) — "
                                 "annulés, confiance -10")

            # Ratio risque/rendement trop faible → confiance réduite
            rr = signal.ratio_risque_rendement
            if rr is not None and rr < 1.0 and action in (ActionSignal.ACHAT, ActionSignal.VENTE):
                signal.confiance = max(0.0, signal.confiance - 10)
                notes.append(f"R/R faible ({rr:.2f}) — confiance -10")

            # Données de mauvaise qualité → confiance plafonnée. La confiance
            # dimensionne le VOLUME de l'ordre : un signal fondé sur peu de
            # données ne doit pas engager une grosse position. Le palier
            # « partielle » était calculé mais n'avait aucun effet.
            if action in (ActionSignal.ACHAT, ActionSignal.VENTE):
                if self.derniere_qualite == "insuffisante" and signal.confiance > 40:
                    signal.confiance = 40.0
                    notes.append("Données insuffisantes — confiance plafonnée à 40")
                elif self.derniere_qualite == "partielle" and signal.confiance > 60:
                    signal.confiance = 60.0
                    notes.append("Données partielles — confiance plafonnée à 60")

            # Taille de position bornée (0 – 20 %)
            if signal.taille_position_pct and not (0 <= signal.taille_position_pct <= 0.2):
                signal.taille_position_pct = max(0.0, min(0.2, signal.taille_position_pct))
                self.nb_corrections += 1
                notes.append("Taille de position bornée à 20%")

            signal.confiance = max(0.0, min(100.0, signal.confiance))

            # Deuxième avis LLM local pour les signaux à forte conviction
            if (action in (ActionSignal.ACHAT, ActionSignal.VENTE)
                    and signal.confiance >= LLM_CONFIANCE_MIN
                    and _peut_consulter_llm()):
                avis = self._avis_ia(signal)
                if avis:
                    self.nb_avis_llm += 1
                    self.dernier_avis = avis["texte"]
                    if avis["verdict"] == "DOUTE":
                        signal.confiance = max(0.0, signal.confiance - 15)
                        notes.append("Avis LLM local : DOUTE — confiance -15")
                    elif avis["verdict"] == "CONFIRME":
                        signal.confiance = min(100.0, signal.confiance + 5)
                        notes.append("Avis LLM local : CONFIRMÉ — confiance +5")

            signal.donnees = dict(signal.donnees or {})
            signal.donnees["assistant"] = {
                "id": self.id,
                "verifications": notes or ["Signal cohérent — aucun ajustement"],
                "avis_llm": self.dernier_avis,
            }
        except Exception as e:
            logger.debug(f"[{self.nom}] Vérification signal : {e}")
        finally:
            self.statut = "idle"
        return signal

    # ── 3. Deuxième avis du moteur IA sélectionné ────────────────────
    def _avis_ia(self, signal: Signal) -> Optional[Dict[str, str]]:
        """Demande un second avis au moteur local choisi (Ollama ou Hermès).

        L'assistant construisait auparavant sa propre requête Ollama : le
        moteur Hermès, sélectionné dans l'interface, était ignoré ici et les
        46 assistants continuaient d'interroger Ollama — donc rien du tout
        quand c'est Hermès qui tourne. Le routage vit désormais dans
        utils/moteur_ia.
        """
        from utils import moteur_ia

        action_fr = "ACHAT" if signal.action == ActionSignal.ACHAT else "VENTE"
        prompt = (
            f"Signal de trading proposé par l'agent « {signal.agent_nom} » :\n"
            f"{action_fr} {signal.symbole} @ {signal.prix_entree or 0:.4f} "
            f"(confiance {signal.confiance:.0f}%, SL {signal.stop_loss or 0:.4f}, "
            f"TP {signal.take_profit or 0:.4f})\n"
            f"Raisonnement : {signal.raisonnement[:200]}\n\n"
            f"Réponds STRICTEMENT au format : CONFIRME ou DOUTE, puis une phrase de justification."
        )
        # 15s max : reste sous le timeout de 30s des agents dans l'orchestrateur
        texte = moteur_ia.chat([
            {"role": "system", "content": "Tu es l'assistant d'un agent de trading. "
                                          "Réponds en français, très concis."},
            {"role": "user", "content": prompt},
        ], timeout=15)
        if not texte:
            return None
        verdict = "DOUTE" if "DOUTE" in texte.upper() else "CONFIRME"
        return {"verdict": verdict, "texte": texte[:200]}

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "nom": self.nom,
            "groupe": self.groupe,
            "statut": self.statut,
            "nb_preparations": self.nb_preparations,
            "nb_verifications": self.nb_verifications,
            "nb_corrections": self.nb_corrections,
            "nb_avis_llm": self.nb_avis_llm,
            "dernier_avis": self.dernier_avis,
            "qualite_donnees": self.derniere_qualite,
        }
