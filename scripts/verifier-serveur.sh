#!/usr/bin/env bash
#
# Vérifie qu'un serveur est correctement configuré pour encaisser les
# abonnements (voir DEPLOIEMENT.md, section 7).
#
#     bash scripts/verifier-serveur.sh https://agence.mondomaine.fr
#
# Ne modifie rien, ne demande aucun secret, n'en affiche aucun.

set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
BASE="${BASE%/}"
ECHECS=0

rouge() { printf '  \033[31m✗\033[0m %s\n' "$*"; ECHECS=$((ECHECS + 1)); }
vert()  { printf '  \033[32m✔\033[0m %s\n' "$*"; }
jaune() { printf '  \033[33m⚠\033[0m %s\n' "$*"; }
titre() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v curl >/dev/null || { echo "curl est requis."; exit 1; }
command -v python3 >/dev/null || { echo "python3 est requis."; exit 1; }

# Extrait une clé du JSON reçu sur l'entrée standard, sans dépendance externe.
champ() { python3 -c "
import json,sys
try: d = json.load(sys.stdin)
except Exception: print(''); raise SystemExit
for c in '$1'.split('.'):
    d = d.get(c, {}) if isinstance(d, dict) else {}
print(d if not isinstance(d, (dict, list)) else '')
"; }

echo "Vérification de $BASE"

titre "1. Le serveur répond"
SANTE="$(curl -fsS -m 10 "$BASE/api/health" 2>/dev/null)" \
  && vert "en ligne — version $(printf '%s' "$SANTE" | champ version)" \
  || rouge "injoignable (service arrêté ? nginx ? pare-feu ?)"

titre "2. HTTPS"
if [[ "$BASE" == https://* ]]; then
  vert "adresse en HTTPS"
else
  jaune "adresse en HTTP — indispensable en HTTPS pour un serveur public"
  jaune "  (webhooks Stripe et cookie de session)"
fi

titre "3. L'application est bien FERMÉE sans compte"
CODE="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$BASE/api/status")"
[[ "$CODE" == "401" ]] \
  && vert "/api/status répond 401 — aucun accès sans compte" \
  || rouge "/api/status répond $CODE au lieu de 401 : l'application est ouverte !"

titre "4. Stripe"
ABO="$(curl -fsS -m 10 "$BASE/api/abonnement" 2>/dev/null)"
if [[ -z "$ABO" ]]; then
  rouge "/api/abonnement injoignable"
else
  [[ "$(printf '%s' "$ABO" | champ verification_auto)" == "True" ]] \
    && vert "STRIPE_SECRET_KEY lue (relecture des sessions possible)" \
    || rouge "STRIPE_SECRET_KEY absente — un retour de paiement ne sera pas vérifié"

  [[ "$(printf '%s' "$ABO" | champ webhook_configure)" == "True" ]] \
    && vert "STRIPE_WEBHOOK_SECRET lue (webhook vérifiable)" \
    || rouge "STRIPE_WEBHOOK_SECRET absente — AUCUN paiement ne sera confirmé"

  if [[ "$(printf '%s' "$ABO" | champ mode_test)" == "True" ]]; then
    jaune "liens de paiement encore en mode TEST — aucun encaissement réel"
    jaune "  → remplacez-les dans python/licence/config.py"
  else
    vert "liens de paiement en production"
  fi
fi

titre "5. Aucun secret exposé"
FUITE=0
for CHEMIN in /api/abonnement /api/licence /api/compte /api/health; do
  CORPS="$(curl -fsS -m 10 "$BASE$CHEMIN" 2>/dev/null || true)"
  if printf '%s' "$CORPS" | grep -qE 'sk_(live|test)_|whsec_'; then
    rouge "un secret Stripe apparaît dans la réponse de $CHEMIN"
    FUITE=1
  fi
done
[[ "$FUITE" -eq 0 ]] && vert "aucun secret dans les réponses publiques"

titre "6. Cookie de session"
ENTETE="$(curl -s -o /dev/null -m 10 -D - -X POST "$BASE/api/compte/connexion" \
          -H 'Content-Type: application/json' \
          -d '{"email":"verification@invalide","mot_de_passe":"x"}' 2>/dev/null \
          | grep -i '^set-cookie' || true)"
if [[ -z "$ENTETE" ]]; then
  # Attendu : des identifiants faux ne posent aucun cookie. On contrôle donc
  # le réglage plutôt que l'en-tête, qui n'existe qu'après une vraie connexion.
  if [[ "$BASE" == https://* ]]; then
    jaune "non vérifiable ici — contrôlez AGENCE_COOKIE_SECURE=1 dans python/.env"
  else
    vert "aucun cookie posé sur des identifiants faux (comportement attendu)"
  fi
else
  printf '%s' "$ENTETE" | grep -qi 'secure' \
    && vert "cookie marqué Secure" \
    || rouge "cookie SANS Secure : le jeton peut circuler en clair"
fi

titre "Résultat"
if [[ "$ECHECS" -eq 0 ]]; then
  printf '\033[32m  Aucun problème bloquant.\033[0m\n'
  echo "  Dernière étape : envoyez un événement de test depuis Stripe"
  echo "  (Développeurs → Webhooks → votre endpoint). Stripe doit afficher 200."
  exit 0
fi
printf '\033[31m  %d problème(s) à corriger — voir DEPLOIEMENT.md.\033[0m\n' "$ECHECS"
exit 1
