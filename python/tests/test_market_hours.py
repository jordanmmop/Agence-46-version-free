"""Tests des heures de marché (dates UTC injectées)."""
import _setup  # noqa: F401
from datetime import datetime, timezone
from utils.market_hours import marche_ouvert, _categorie


def _dt(annee, mois, jour, heure, minute=0):
    return datetime(annee, mois, jour, heure, minute, tzinfo=timezone.utc)


def run():
    print("═══ Heures de marché ═══")
    # catégories
    assert _categorie("BTC-USD") == "crypto"
    assert _categorie("EURUSD=X") == "forex"
    assert _categorie("AAPL") == "action"
    assert _categorie("^GSPC") == "indice"
    print("  OK — classification crypto/forex/action/indice")

    # Toute paire déclarée dans SYMBOLES_FOREX doit être reconnue comme forex,
    # AU FORMAT YAHOO comme au FORMAT COURTIER : l'utilisateur saisit parfois
    # le nom vu dans le Market Watch MetaTrader (USDSEK), et le journal comme
    # les positions renvoient ce nom-là.
    from config import SYMBOLES_FOREX
    for paire in SYMBOLES_FOREX:
        assert _categorie(paire) == "forex", f"{paire} mal classé"
        brut = paire.replace("=X", "")            # nom affiché par le courtier
        assert _categorie(brut) == "forex", (
            f"{brut} (format courtier) classé « {_categorie(brut)} » : "
            f"l'auto-trader le sauterait hors séance US alors que le forex "
            f"est ouvert du dimanche 22h au vendredi 22h UTC")

    # Paires du Market Watch AvaTrade, y compris une devise « exotique » :
    # USDSEK retombait sur « action » faute de SEK dans la table des devises.
    for brut in ("EURUSD", "GBPUSD", "USDCHF", "USDJPY", "AUDUSD",
                 "NZDUSD", "USDCAD", "USDSEK", "USDNOK", "USDMXN"):
        assert _categorie(brut) == "forex", f"{brut} mal classé"
    assert _categorie("BTCUSD") == "crypto"       # nom courtier du Bitcoin
    print("  OK — paires du courtier (dont USDSEK) reconnues comme forex")

    # Matières premières : classées « action » avant leur intégration, elles
    # étaient sautées par l'auto-trader hors séance US (13h30-20h UTC) — soit
    # pendant les sessions asiatique et européenne, où l'or et le pétrole
    # cotent pourtant. Le nom du Market Watch (GOLD.TR, CrudeOIL) doit être
    # reconnu aussi bien que le ticker de cours (GC=F, CL=F).
    from config import SYMBOLES_MATIERES
    for sym in SYMBOLES_MATIERES:
        assert _categorie(sym) == "matiere", f"{sym} mal classé"
    for brut in ("GOLD.TR", "gold.tr", "GOLD", "XAUUSD", "CrudeOIL",
                 "CRUDEOIL", "WTI", "USOIL", "SILVER", "XAGUSD", "NATGAS"):
        assert _categorie(brut) == "matiere", (
            f"{brut} classé « {_categorie(brut)} » : l'auto-trader le "
            f"traiterait comme une action et le sauterait hors séance US")
    print("  OK — or et pétrole reconnus comme matières premières")

    samedi = _dt(2026, 7, 18, 15)       # samedi 15h UTC
    mercredi_seance = _dt(2026, 7, 15, 15)   # mercredi 15h UTC (séance US)
    mercredi_nuit = _dt(2026, 7, 15, 23)     # mercredi 23h UTC (hors séance)
    dimanche_matin = _dt(2026, 7, 19, 10)    # dimanche 10h UTC
    dimanche_soir = _dt(2026, 7, 19, 23)     # dimanche 23h UTC

    # crypto : toujours ouvert
    assert marche_ouvert("BTC-USD", samedi)[0]
    assert marche_ouvert("BTC-USD", mercredi_nuit)[0]
    print("  OK — crypto 24/7")

    # actions : week-end fermé, séance US ouverte, nuit fermée
    ok, raison = marche_ouvert("AAPL", samedi)
    assert not ok and "week-end" in raison
    assert marche_ouvert("AAPL", mercredi_seance)[0]
    ok, raison = marche_ouvert("AAPL", mercredi_nuit)
    assert not ok and "séance" in raison
    print("  OK — actions : week-end et hors séance bloqués")

    # forex : samedi fermé, dimanche matin fermé, dimanche 23h ouvert
    assert not marche_ouvert("EURUSD=X", samedi)[0]
    assert not marche_ouvert("EURUSD=X", dimanche_matin)[0]
    assert marche_ouvert("EURUSD=X", dimanche_soir)[0]
    assert marche_ouvert("EURUSD=X", mercredi_nuit)[0]
    print("  OK — forex : cycle dimanche 22h → vendredi 22h UTC")

    # matières premières : fenêtre propre (dimanche 23h → vendredi 21h UTC),
    # coupure quotidienne 21h-22h UTC. Sans elle, l'or serait traité comme une
    # action américaine : fermé le matin européen, fermé la nuit asiatique.
    assert not marche_ouvert("GC=F", samedi)[0]
    assert not marche_ouvert("GC=F", dimanche_matin)[0]
    ok, raison = marche_ouvert("GC=F", dimanche_soir)          # dimanche 23h
    assert ok, raison
    assert marche_ouvert("GC=F", _dt(2026, 7, 15, 3))[0]       # nuit asiatique
    assert marche_ouvert("CL=F", _dt(2026, 7, 15, 9))[0]       # matinée européenne
    ok, raison = marche_ouvert("GC=F", _dt(2026, 7, 15, 21, 30))
    assert not ok and "coupure" in raison, raison
    ok, raison = marche_ouvert("CL=F", _dt(2026, 7, 17, 21, 30))   # vendredi
    assert not ok and "vendredi" in raison, raison
    # le nom du courtier suit la même règle que le ticker de cours
    assert marche_ouvert("GOLD.TR", _dt(2026, 7, 15, 3))[0]
    assert marche_ouvert("CrudeOIL", _dt(2026, 7, 15, 9))[0]
    print("  OK — matières premières : dimanche 23h → vendredi 21h UTC")

    # AUDIT : la séance US suit le changement d'heure (avant, figée en hiver).
    # Été (14 juil) : NYSE 13h30-20h UTC. Hiver (14 jan) : 14h30-21h UTC.
    assert marche_ouvert("AAPL", _dt(2026, 7, 14, 13, 45))[0]      # été : ouvert à 13h45
    assert not marche_ouvert("AAPL", _dt(2026, 7, 14, 20, 30))[0]  # été : fermé à 20h30
    assert not marche_ouvert("AAPL", _dt(2026, 1, 14, 13, 45))[0]  # hiver : fermé à 13h45
    assert marche_ouvert("AAPL", _dt(2026, 1, 14, 20, 30))[0]      # hiver : ouvert à 20h30
    print("  OK — séance US ajustée au changement d'heure (été/hiver)")


if __name__ == "__main__":
    run()
    print("✅ Heures de marché OK")
