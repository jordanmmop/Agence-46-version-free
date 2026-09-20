"""Vérification AVANT trading réel — « pré-vol ».

Aucun audit de code ne peut deviner comment VOTRE courtier nomme ses symboles,
quel est son spread, ni quel volume votre compte autorise. Ce module rejoue le
chemin de décision COMPLET sur votre compte réellement connecté et rapporte,
pour chaque symbole, ce qui serait envoyé — SANS ENVOYER AUCUN ORDRE.

Il répond à la seule question qui compte avant d'engager de l'argent :
« si le robot déclenchait un achat maintenant, que se passerait-il exactement ? »

Lecture seule : aucun order_send, aucune modification d'état.
"""
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OK, ATTENTION, ERREUR = "ok", "attention", "erreur"


def _c(statut: str, titre: str, detail: str = "", conseil: str = "") -> Dict[str, Any]:
    return {"statut": statut, "titre": titre, "detail": detail, "conseil": conseil}


_ORDRE_STATUT = {OK: 0, ATTENTION: 1, ERREUR: 2}


def _degrader(res: Dict[str, Any], niveau: str) -> None:
    """Élève le statut d'un symbole sans JAMAIS le rétrograder.

    Plusieurs contrôles s'enchaînent sur le même symbole : une ERREUR
    (ex. données simulées sur compte réel) ne doit pas être écrasée par une
    ATTENTION émise plus loin (spread, lot minimum, perte au stop), sinon le
    verdict global annoncerait « fonctionnel » sur une condition bloquante.
    """
    if _ORDRE_STATUT[niveau] > _ORDRE_STATUT.get(res.get("statut", OK), 0):
        res["statut"] = niveau


def _env_float(nom: str, defaut: float) -> float:
    """float(os.getenv(...)) tolérant : accepte la virgule décimale et retombe
    sur le défaut si la valeur est inexploitable."""
    try:
        return float(str(os.getenv(nom, defaut)).replace(",", "."))
    except (TypeError, ValueError):
        return defaut


def _reglage(cle: str, defaut):
    """Réglage de performance en vigueur (Réglages ⚙️ → Moteur de performance).

    Le pré-vol doit décrire ce qui se passerait RÉELLEMENT : lire ici les
    anciennes variables d'environnement annoncerait un refus (« risque > 1 % »)
    là où l'exécution laisserait maintenant passer l'ordre."""
    try:
        from utils import trading_config
        return trading_config.get(cle, defaut)
    except Exception:
        return defaut


def _verifier_compte(mt5) -> List[Dict[str, Any]]:
    checks = []
    st = mt5.get_status()
    info = st.get("account_info") or {}
    if not st.get("connected"):
        checks.append(_c(ERREUR, "Compte non connecté",
                         "Aucun compte AvaTrade n'est connecté.",
                         "Cliquez « Connexion AvaTrade » pour vous connecter."))
        return checks

    reel = mt5.est_reel()
    demo = mt5.est_demo() if reel else False
    devise = info.get("currency", "?")
    equity = mt5.equity_live()
    if reel and demo:
        # Vraie connexion MetaTrader, mais argent FICTIF : annoncer « argent
        # réel » ici serait faux — et l'utilisateur finirait par ignorer
        # l'avertissement le jour où il porte sur un compte financé.
        checks.append(_c(OK, "Compte DÉMO connecté",
                         f"Compte {info.get('account', '?')} · {info.get('server', '?')} · "
                         f"équité {equity:.2f} {devise} (argent fictif)"))
        checks.append(_c(OK, "Les ordres partent au courtier, sans argent réel",
                         "Le comportement est identique au réel : c'est ici qu'il faut "
                         "valider les symboles, les volumes et les stops.",
                         "Passez en compte financé seulement après plusieurs jours "
                         "de fonctionnement conforme."))
    elif reel:
        checks.append(_c(OK, "Compte RÉEL connecté",
                         f"Compte {info.get('account', '?')} · {info.get('server', '?')} · "
                         f"équité {equity:.2f} {devise}"))
        checks.append(_c(ATTENTION, "Les ordres seront RÉELS",
                         "Chaque signal des agents engagera de l'argent réel.",
                         "Commencez sur un compte DÉMO pour valider le comportement."))
    else:
        checks.append(_c(ATTENTION, "Mode simulation",
                         f"Les ordres sont journalisés localement, PAS envoyés au courtier "
                         f"(équité affichée {equity:.2f} {devise}).",
                         "Pour du réel : installez MetaTrader 5 (Réglages → Installer "
                         "MetaTrader 5), puis connectez votre compte AvaTrade."))
    if reel and equity <= 0:
        checks.append(_c(ERREUR, "Équité nulle ou illisible",
                         "Le dimensionnement du risque est impossible sans équité.",
                         "Vérifiez la connexion au compte."))

    # Bouton « AlgoTrading » du terminal : cause n°1 d'ordres rejetés (10027).
    # Le message brut du courtier — « AutoTrading disabled by client » — ne dit
    # pas où cliquer, et l'utilisateur découvre le blocage seulement après un
    # premier ordre refusé. Ce contrôle le remonte AVANT.
    try:
        algo = mt5.algotrading_actif()
    except Exception:
        algo = None
    if algo is False:
        checks.append(_c(ERREUR, "Bouton « AlgoTrading » désactivé",
                         "Le terminal MetaTrader 5 refusera TOUS les ordres du "
                         "robot (code 10027).",
                         "Ouvrez MetaTrader 5 → barre d'outils → « AlgoTrading » "
                         "(Ctrl+E) : le bouton doit devenir vert."))
    elif algo is True:
        checks.append(_c(OK, "AlgoTrading autorisé",
                         "Le terminal MetaTrader 5 accepte les ordres du robot."))
    return checks


def _verifier_garde_fous() -> List[Dict[str, Any]]:
    checks = []
    try:
        from utils.risk_guard import get_risk_guard
        rs = get_risk_guard().get_status()
    except Exception as e:
        return [_c(ERREUR, "Garde-fous illisibles", str(e))]

    if not rs.get("actif"):
        checks.append(_c(ATTENTION, "Garde-fous DÉSACTIVÉS",
                         "Le kill-switch de perte journalière et les plafonds de "
                         "positions/exposition ne s'appliquent pas : rien n'arrêtera "
                         "le robot en cas de série perdante.",
                         "Réactivez-les dans Réglages ⚙️ → Sécurité si vous voulez "
                         "un filet automatique."))
    else:
        checks.append(_c(OK, "Garde-fous actifs",
                         f"Perte max/jour {rs.get('perte_max_pct')}% · "
                         f"max {rs.get('max_positions')} positions · "
                         f"exposition max {rs.get('exposition_max_pct')}%"))
    if rs.get("kill_switch"):
        checks.append(_c(ERREUR, "Kill-switch DÉCLENCHÉ",
                         rs.get("raison", ""),
                         "Aucun ordre ne partira tant qu'il n'est pas réarmé."))

    # Conversions protégées : une variable mal saisie (virgule décimale,
    # texte) ne doit pas faire échouer TOUT le diagnostic par un 500.
    risque = float(_reglage("risque_par_trade_pct", 25.0))
    sl_def = float(_reglage("sl_pct", 2.0))
    spread_max = float(_reglage("spread_max_pct", 2.0))
    statut = OK if risque <= 2.0 else ATTENTION
    checks.append(_c(statut, "Risque par ordre",
                     f"{risque}% de l'équité maximum perdu si le stop est touché "
                     f"(stop par défaut à {sl_def}% du prix, spread max {spread_max}%)",
                     "Au-delà de 2% par trade, une série de pertes devient rapidement "
                     "difficile à rattraper." if statut == ATTENTION else ""))

    # Levier : ce que les agents peuvent engager, et ce qu'ils engagent là.
    try:
        from utils import trading_config
        cfg = trading_config.tout()
        checks.append(_c(OK if cfg["levier_max"] <= 5 else ATTENTION,
                         f"Levier x{cfg['levier_actuel']} (plafond x{cfg['levier_max']})",
                         f"Chaque ordre engage jusqu'à {cfg['levier_actuel']}× l'équité "
                         f"du compte en notionnel · pilotage "
                         f"{'automatique par les agents' if cfg['levier_auto'] else 'manuel'}.",
                         "À levier 10, un mouvement de 10% contre la position efface "
                         "l'équivalent du capital engagé."
                         if cfg["levier_max"] > 5 else ""))
        obj = float(cfg["objectif_journalier"])
        cap = float(cfg["capital_reference"])
        if obj > 0 and cap > 0:
            vise = obj / cap * 100
            checks.append(_c(OK if vise <= 5 else ATTENTION,
                             f"Objectif du jour {obj:.0f} € pour {cap:.0f} € de capital",
                             f"Soit {vise:.0f}% par jour.",
                             "Aucun réglage ne peut garantir ce rendement : au-delà de "
                             "quelques pourcents par jour, il dépend entièrement du "
                             "marché." if vise > 5 else ""))
    except Exception:
        pass
    return checks


def _verifier_symbole(mt5, symbole: str) -> Dict[str, Any]:
    """Rejoue la décision complète pour un symbole, sans envoyer d'ordre."""
    res: Dict[str, Any] = {"symbole": symbole, "statut": OK, "messages": []}

    # 1. Le courtier connaît-il ce symbole ?
    nom_courtier: Optional[str] = None
    backend_courtier = bool(getattr(mt5, "_lib_available", False))
    if backend_courtier:
        try:
            nom_courtier = mt5.resoudre_symbole(symbole)
        except Exception as e:
            res["messages"].append(f"Résolution impossible : {e}")

    if not backend_courtier:
        # Mode simulation : aucun courtier à interroger. On poursuit quand même
        # les autres contrôles (données, stop-loss, volume) qui restent utiles.
        _degrader(res, ATTENTION)
        res["messages"].append(
            "Mode simulation : le nom du symbole chez le courtier ne peut pas être "
            "vérifié. Relancez cette vérification une fois le compte connecté.")
    elif not nom_courtier:
        _degrader(res, ERREUR)
        res["messages"].append(
            "Symbole INTROUVABLE chez votre courtier — aucun ordre ne partira. "
            "Ouvrez MetaTrader → Market Watch pour voir le nom exact utilisé.")
        return res
    if nom_courtier:
        res["nom_courtier"] = nom_courtier
    if nom_courtier and (nom_courtier.upper().replace(".", "").replace("-", "") !=
                         symbole.upper().replace("-", "").replace("=X", "").replace(".", "")):
        res["messages"].append(f"Traduit en « {nom_courtier} » chez le courtier — vérifiez "
                               f"que c'est bien l'instrument voulu.")

    # 2. Marché ouvert ?
    try:
        from utils.market_hours import marche_ouvert
        ouvert, raison = marche_ouvert(symbole)
        res["marche_ouvert"] = ouvert
        if not ouvert:
            res["messages"].append(f"Marché fermé : {raison} (aucun ordre pour l'instant)")
    except Exception:
        res["marche_ouvert"] = None

    # 3. Données de marché réelles ou inventées ?
    try:
        from utils.market_data import FetcheurDonnees
        md = FetcheurDonnees.obtenir_donnees(symbole, "1h")
        if md is None:
            _degrader(res, ERREUR)
            res["messages"].append("Aucune donnée de marché disponible.")
            return res
        # D'où viennent les cours ? C'est LA question quand plus aucun ordre ne
        # part : des prix « simulés » font refuser tous les ordres sur un compte
        # connecté, sans que rien ne l'indique nulle part.
        res["source_cours"] = md.indicateurs.get("source") or "Yahoo"
        if md.indicateurs.get("simule"):
            _degrader(res, ERREUR if mt5.est_reel() else ATTENTION)
            res["messages"].append(
                "Données de marché INDISPONIBLES (prix simulés) — sur un compte "
                "connecté, TOUS les ordres seront refusés sur ce symbole. "
                "Connectez MetaTrader 5 : les cours seront alors lus chez le "
                "courtier, sans dépendre de Yahoo.")
        else:
            res["messages"].append(
                f"Cours réels lus via {res['source_cours']}.")
        prix = float(md.prix_actuel or 0)
    except Exception as e:
        _degrader(res, ERREUR)
        res["messages"].append(f"Données de marché illisibles : {e}")
        return res
    res["prix"] = round(prix, 6)
    if not prix:
        _degrader(res, ERREUR)
        res["messages"].append("Prix de référence nul — ordre impossible.")
        return res

    # 4. Stop-loss / take-profit qui SERAIENT appliqués
    from utils.auto_trader import completer_sl_tp
    sl, tp = completer_sl_tp("BUY", prix, 0.0, 0.0)
    res["stop_loss"] = round(sl, 6)
    res["take_profit"] = round(tp, 6)

    # 5. Volume qui SERAIT envoyé + perte au stop correspondante
    equity = mt5.equity_live()
    # Point de départ : ce que l'auto-trader demanderait à 60 % de confiance
    # (60 % du « volume max » réglé). L'exécution le recalcule ensuite au
    # levier ; ici on vérifie surtout le plafond de risque et le lot minimum.
    volume_souhaite = round(float(_reglage("volume_max_lot", 5.0)) * 0.6, 2) or 0.3
    volume_final = volume_souhaite
    try:
        if getattr(mt5, "_lib_available", False):
            import MetaTrader5 as _m
            # SOUS LE VERROU du gestionnaire : le pré-vol peut être lancé
            # pendant que la boucle auto-trader envoie un ordre, et la
            # connexion terminal MetaTrader5 est unique et non thread-safe.
            with mt5.verrou:
                info = _m.symbol_info(nom_courtier)
                tick = _m.symbol_info_tick(nom_courtier) if info is not None else None
            if info is not None:
                plafond = mt5._plafonner_volume_risque(
                    volume_souhaite, prix, sl, equity, info)
                vmin = getattr(info, "volume_min", 0.01) or 0.01
                if plafond is None:
                    # Risque non vérifiable. Le comportement dépend du réglage
                    # « exiger un risque calculable » : refus, ou envoi au
                    # volume demandé sans plafond vérifiable.
                    if _reglage("exiger_risque_calculable", False):
                        _degrader(res, ERREUR)
                        res["messages"].append(
                            "Perte au stop INCALCULABLE : le courtier n'expose ni la "
                            "valeur du tick ni la taille de contrat pour ce symbole. "
                            "L'ordre sera REFUSÉ (la limite de risque par ordre ne "
                            "peut pas être garantie). Ajoutez le symbole au Market "
                            "Watch de MetaTrader pour charger ses spécifications.")
                        volume_final = 0.0
                        plafond = 0.0
                    else:
                        _degrader(res, ATTENTION)
                        res["messages"].append(
                            "Perte au stop INCALCULABLE (le courtier n'expose ni la "
                            "valeur du tick ni la taille de contrat) : l'ordre partira "
                            "quand même au volume demandé, SANS plafond de risque "
                            "vérifiable — réglage « exiger un risque calculable » "
                            "désactivé.")
                        plafond = volume_souhaite
                        volume_final = volume_souhaite
                else:
                    volume_final = plafond
                if plafond and volume_final < vmin:
                    _degrader(res, ATTENTION)
                    # Le volume calculé (ex. 0.0004) n'est PAS envoyable : le
                    # courtier impose un minimum. Publier ce chiffre — et une
                    # perte au stop calculée dessus — décrivait un ordre qui ne
                    # partira jamais, en affichant une perte rassurante alors
                    # que c'est justement son ampleur au lot minimum qui motive
                    # le refus. On rapporte donc le lot MINIMUM, seul volume
                    # réellement tentable, et la perte correspondante.
                    res["volume_calcule"] = round(float(volume_final or 0), 4)
                    refuse = not _reglage("autoriser_lot_minimum", True)
                    res["volume_refuse"] = refuse
                    volume_final = vmin
                    if refuse:
                        res["messages"].append(
                            f"Le lot minimum ({vmin}) dépasse déjà votre budget de risque : "
                            f"l'ordre sera REFUSÉ. Le volume tenu par le risque serait "
                            f"{res['volume_calcule']} lot, en dessous du minimum du "
                            f"courtier. Augmentez le capital, le « risque par ordre », "
                            f"ou activez « autoriser le lot minimum ».")
                    else:
                        res["messages"].append(
                            f"Le lot minimum ({vmin}) dépasse votre budget de risque "
                            f"({res['volume_calcule']} lot) : l'ordre partira QUAND MÊME "
                            f"au lot minimum — la perte au stop sera donc supérieure au "
                            f"pourcentage réglé (« autoriser le lot minimum » est actif).")
                # spread courant (tick lu plus haut, sous le même verrou)
                if tick and tick.ask and tick.bid:
                    milieu = (tick.ask + tick.bid) / 2
                    spread_pct = (tick.ask - tick.bid) / milieu * 100 if milieu else 0
                    res["spread_pct"] = round(spread_pct, 4)
                    # _env_float (et non float()) : une valeur saisie avec une
                    # virgule décimale levait ValueError ICI, et le `except`
                    # global de ce bloc la rapportait comme « dimensionnement
                    # non simulable » — sautant EN SILENCE les deux contrôles
                    # les plus importants (lot minimum et spread).
                    seuil = float(_reglage("spread_max_pct", 2.0))
                    if seuil > 0 and spread_pct > seuil:
                        _degrader(res, ATTENTION)
                        res["messages"].append(
                            f"Spread actuel {spread_pct:.3f}% > seuil {seuil}% : "
                            f"l'ordre serait refusé maintenant (marché peu liquide).")
    except Exception as e:
        res["messages"].append(f"Dimensionnement non simulable : {e}")

    res["volume"] = round(float(volume_final or 0), 4)
    if res["volume"] and prix and sl:
        # Perte au stop : publiée UNIQUEMENT si réellement calculable.
        # Une distance de prix multipliée par un nombre de LOTS (sans taille de
        # contrat) sous-évalue la perte d'un facteur 100 000 sur le Forex :
        # mieux vaut n'afficher aucun chiffre qu'un faux feu vert sur la valeur
        # qui justifie l'existence de ce module.
        perte = None
        try:
            if getattr(mt5, "_lib_available", False) and nom_courtier:
                import MetaTrader5 as _m
                with mt5.verrou:
                    info = _m.symbol_info(nom_courtier)
                if info is not None:
                    dist = abs(prix - sl)
                    ts = getattr(info, "trade_tick_size", 0) or 0
                    tv = getattr(info, "trade_tick_value", 0) or 0
                    cs = getattr(info, "trade_contract_size", 0) or 0
                    if ts > 0 and tv > 0:
                        perte = dist / ts * tv * res["volume"]      # devise du COMPTE
                    elif cs > 0:
                        perte = dist * cs * res["volume"]           # devise de COTATION
                        res["messages"].append(
                            "Perte au stop estimée via la taille de contrat (valeur du "
                            "tick indisponible) : exprimée en devise de cotation, elle "
                            "peut différer de la devise du compte.")
        except Exception as e:
            res["messages"].append(f"Perte au stop non calculable : {e}")

        if perte is None:
            _degrader(res, ATTENTION)
            res["messages"].append(
                "Perte au stop NON CALCULABLE sans les spécifications du courtier "
                "(taille de contrat / valeur du tick). Connectez MetaTrader 5 et "
                "ajoutez le symbole au Market Watch, puis relancez cette "
                "vérification : un montant affiché ici serait faux.")
        else:
            res["perte_au_stop"] = round(perte, 2)
            if res.get("volume_refuse"):
                res["messages"].append(
                    f"Perte affichée au LOT MINIMUM ({res['volume']}) — c'est "
                    f"cette ampleur qui fait refuser l'ordre, pas le volume "
                    f"théorique.")
            if equity > 0:
                res["perte_au_stop_pct"] = round(perte / equity * 100, 3)
                # _env_float : hors de tout `try` ici — un float() sur une
                # valeur à virgule décimale faisait échouer la vérification
                # ENTIÈRE du symbole (« Vérification impossible »).
                limite = float(_reglage("risque_par_trade_pct", 25.0))
                if res["perte_au_stop_pct"] > limite * 1.5:
                    _degrader(res, ATTENTION)
                    res["messages"].append(
                        f"Perte estimée au stop {res['perte_au_stop_pct']:.2f}% > limite "
                        f"annoncée {limite}% — vérifiez les specs du symbole.")

    if not res["messages"]:
        res["messages"].append("Tout est cohérent pour ce symbole.")
    return res


def verifier(symboles: List[str] = None) -> Dict[str, Any]:
    """Vérification complète avant trading réel. N'ENVOIE AUCUN ORDRE."""
    from utils.mt5_manager import get_mt5_manager
    mt5 = get_mt5_manager()

    if not symboles:
        try:
            from config import SYMBOLES_DEFAULT
            symboles = list(SYMBOLES_DEFAULT[:3])
        except Exception:
            symboles = ["BTC-USD"]
    else:
        # Accepte le nom AFFICHÉ par l'interface (BTCUSD, AUDUSD) : c'est
        # celui que l'utilisateur recopie naturellement. Sans traduction,
        # yfinance ne trouvait rien et la vérification portait sur des prix
        # inventés (Bitcoin à 89, AUDUSD à 128) — exactement le contraire de
        # ce qu'un contrôle avant trading réel doit produire.
        try:
            from config import vers_symbole_yahoo
            symboles = [vers_symbole_yahoo(s) for s in symboles]
        except Exception:
            pass

    checks = _verifier_compte(mt5) + _verifier_garde_fous()

    # Notifications : utile pour être prévenu d'un kill-switch
    try:
        from utils.notifier import canaux_configures
        if canaux_configures():
            checks.append(_c(OK, "Alertes configurées",
                             "Vous serez prévenu des ordres et du kill-switch."))
        else:
            checks.append(_c(ATTENTION, "Aucune alerte configurée",
                             "Vous ne serez pas prévenu si la sécurité coupe le trading.",
                             "Réglages → Notifications (webhook ou push mobile)."))
    except Exception:
        pass

    symbs = []
    for s in symboles:
        try:
            symbs.append(_verifier_symbole(mt5, s))
        except Exception as e:
            # Un symbole en échec ne doit pas faire perdre TOUT le diagnostic.
            logger.warning(f"[Pré-vol] {s} : {e}")
            symbs.append({"symbole": s, "statut": ERREUR,
                          "messages": [f"Vérification impossible pour ce symbole : {e}"]})

    erreurs = [c for c in checks if c["statut"] == ERREUR] + \
              [s for s in symbs if s["statut"] == ERREUR]
    alertes = [c for c in checks if c["statut"] == ATTENTION] + \
              [s for s in symbs if s["statut"] == ATTENTION]

    if erreurs:
        verdict = "bloquant"
        resume = (f"{len(erreurs)} problème(s) empêchent un fonctionnement correct. "
                  f"Corrigez-les avant de trader.")
    elif alertes:
        verdict = "avertissement"
        resume = (f"Fonctionnel, mais {len(alertes)} point(s) méritent votre "
                  f"attention avant d'engager de l'argent réel.")
    else:
        verdict = "pret"
        resume = "Tout est cohérent — aucun problème détecté."

    return {
        "verdict": verdict,
        "resume": resume,
        "reel": mt5.est_reel(),
        "checks": checks,
        "symboles": symbs,
        "nb_erreurs": len(erreurs),
        "nb_alertes": len(alertes),
    }
