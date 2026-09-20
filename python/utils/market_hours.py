"""Heures de marché — évite de tenter des ordres quand la place est fermée.

L'auto-trader saute les symboles dont le marché est fermé au lieu
d'envoyer des ordres voués au rejet par le courtier :
- crypto : 24h/24, 7j/7
- forex  : du dimanche 22h UTC au vendredi 22h UTC
- matières premières (or, pétrole) : du dimanche 23h UTC au vendredi 21h UTC,
  avec la coupure quotidienne de 21h-22h UTC des CFD métaux/énergie
- actions / indices : séance US, lun-ven, ajustée au changement d'heure
  (13h30-20h UTC en été, 14h30-21h UTC en hiver). Les jours fériés US ne
  sont pas gérés : le courtier reste l'arbitre final.

Désactivable avec HEURES_MARCHE_ACTIF=off.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


def _us_dst(now: datetime) -> bool:
    """L'heure d'été US est-elle en vigueur ? (2e dimanche de mars → 1er
    dimanche de novembre). Pendant cette période le NYSE ouvre une heure plus
    tôt en UTC (13h30-20h au lieu de 14h30-21h) — ~8 mois par an."""
    a = now.year
    mars = datetime(a, 3, 8, tzinfo=timezone.utc)          # 2e dimanche de mars
    debut = mars + timedelta(days=(6 - mars.weekday()) % 7)
    nov = datetime(a, 11, 1, tzinfo=timezone.utc)          # 1er dimanche de novembre
    fin = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return debut <= now < fin


# Matières premières reconnues au nom du COURTIER (Market Watch MetaTrader),
# normalisées sans séparateur : « GOLD.TR » y entre comme « GOLDTR ». Sans
# cette table, l'or et le pétrole retombaient sur « action » et l'auto-trader
# les sautait hors de la séance US (13h30-20h UTC) — c'est-à-dire pendant les
# sessions asiatique et européenne, où ils cotent pourtant normalement.
_MATIERES = {
    # métaux
    "GOLD", "GOLDTR", "XAUUSD", "GOLDUSD", "XAU",
    "SILVER", "SILVERTR", "XAGUSD", "XAG",
    "PLATINUM", "XPTUSD", "PALLADIUM", "XPDUSD", "COPPER",
    # énergie
    "CRUDEOIL", "CRUDEOILTR", "WTI", "WTIUSD", "USOIL", "OILWTI",
    "BRENT", "BRENTOIL", "UKOIL", "NATGAS", "NATURALGAS",
}


def _normaliser(symbole: str) -> str:
    """Nom d'instrument sans séparateur, en majuscules (« GOLD.TR » →
    « GOLDTR ») : les courtiers décorent leurs symboles, pas les tables."""
    s = symbole.upper()
    for c in "-._#/ ":
        s = s.replace(c, "")
    return s


def _categorie(symbole: str) -> str:
    s = symbole.upper()
    try:
        from config import (SYMBOLES_CRYPTO, SYMBOLES_ACTIONS,
                            SYMBOLES_FOREX, SYMBOLES_INDICES,
                            SYMBOLES_MATIERES)
        if s in {x.upper() for x in SYMBOLES_CRYPTO}:
            return "crypto"
        if s in {x.upper() for x in SYMBOLES_FOREX}:
            return "forex"
        if s in {x.upper() for x in SYMBOLES_MATIERES}:
            return "matiere"
        if s in {x.upper() for x in SYMBOLES_INDICES}:
            return "indice"
        if s in {x.upper() for x in SYMBOLES_ACTIONS}:
            return "action"
    except Exception:
        pass
    # heuristiques pour les symboles hors listes
    if s.endswith("=X"):
        return "forex"
    # Contrat à terme Yahoo (GC=F, CL=F, SI=F) ou nom courtier d'une matière
    # première (GOLD.TR, CrudeOIL, XAUUSD).
    if s.endswith("=F") or _normaliser(s) in _MATIERES:
        return "matiere"
    # Paire de devises BRUTE (EURUSD, GBPJPY, USDSEK...), c'est-à-dire le nom
    # tel que le courtier l'affiche dans le Market Watch — sans suffixe Yahoo.
    # Les devises « exotiques » comptent : sans SEK, « USDSEK » retombait sur
    # « action » et l'auto-trader sautait la paire hors séance US (14h30-21h
    # UTC) alors que le forex est ouvert du dimanche 22h au vendredi 22h.
    _DEVISES = {
        # majeures
        "EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD",
        # scandinaves et européennes
        "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "ISK",
        # autres paires couramment proposées par les courtiers
        "MXN", "ZAR", "TRY", "SGD", "HKD", "CNH", "ILS", "THB",
    }
    if len(s) == 6 and s[:3] in _DEVISES and s[3:] in _DEVISES:
        return "forex"
    if s.startswith("^"):
        return "indice"
    if "-USD" in s or "USDT" in s or "BTC" in s or "ETH" in s:
        return "crypto"
    return "action"


def marche_ouvert(symbole: str, quand: Optional[datetime] = None) -> Tuple[bool, str]:
    """(ouvert, raison si fermé). `quand` en UTC — injectable pour les tests."""
    if os.getenv("HEURES_MARCHE_ACTIF", "true").lower() not in ("1", "true", "yes", "oui"):
        return True, ""
    now = quand or datetime.now(timezone.utc)
    # Un datetime NAÏF (sans fuseau) est interprété comme de l'UTC. Sans ce
    # garde, `_us_dst()` comparait un naïf à des bornes tz-aware et levait
    # « can't compare offset-naive and offset-aware datetimes » — une
    # exception qui remontait jusqu'au cycle de l'auto-trader et interrompait
    # l'analyse des symboles suivants. `datetime.utcnow()` (naïf) est l'appel
    # que tout le monde écrit spontanément : il doit fonctionner.
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cat = _categorie(symbole)
    jour = now.weekday()              # 0 = lundi … 6 = dimanche
    heure = now.hour + now.minute / 60

    if cat == "crypto":
        return True, ""

    if cat == "forex":
        if jour == 5:                                   # samedi
            return False, "forex fermé le samedi"
        if jour == 6 and heure < 22:                    # dimanche avant 22h UTC
            return False, "forex ouvre dimanche 22h UTC"
        if jour == 4 and heure >= 22:                   # vendredi après 22h UTC
            return False, "forex fermé vendredi 22h UTC"
        return True, ""

    if cat == "matiere":
        # Métaux et énergie chez un courtier CFD : cotation continue du
        # dimanche 23h UTC au vendredi 21h UTC, avec la coupure de service
        # quotidienne de 21h-22h UTC (roulement des contrats). Fenêtre proche
        # du forex, mais PAS identique : l'or rouvre le dimanche une heure
        # plus tard et ferme le vendredi une heure plus tôt.
        if jour == 5:                                   # samedi
            return False, "matières premières fermées le samedi"
        if jour == 6 and heure < 23:                    # dimanche avant 23h UTC
            return False, "matières premières ouvrent dimanche 23h UTC"
        if jour == 4 and heure >= 21:                   # vendredi après 21h UTC
            return False, "matières premières fermées vendredi 21h UTC"
        if 21 <= heure < 22:
            return False, "coupure quotidienne des matières premières (21h-22h UTC)"
        return True, ""

    # actions / indices : séance US (9h30-16h heure de New York), convertie en
    # UTC selon l'heure d'été. Les jours fériés US ne sont pas gérés ici — le
    # courtier rejette l'ordre ce jour-là (arbitre final).
    if jour >= 5:
        return False, "bourse fermée le week-end"
    if _us_dst(now):
        ouv, fer = 13.5, 20.0          # heure d'été (EDT = UTC-4)
    else:
        ouv, fer = 14.5, 21.0          # heure d'hiver (EST = UTC-5)
    if not (ouv <= heure < fer):
        h_o = f"{int(ouv):02d}h{int(round((ouv % 1) * 60)):02d}"
        h_f = f"{int(fer):02d}h{int(round((fer % 1) * 60)):02d}"
        return False, f"hors séance US ({h_o}-{h_f} UTC)"
    return True, ""
