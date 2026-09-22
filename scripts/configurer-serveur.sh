#!/usr/bin/env bash
#
# Génère python/.env pour un déploiement serveur (voir DEPLOIEMENT.md).
#
# À lancer SUR LE SERVEUR, depuis le dossier de l'application :
#     bash scripts/configurer-serveur.sh
#
# Le script demande les valeurs, les contrôle, écrit le fichier en mode 600 et
# n'affiche JAMAIS les secrets saisis. Il n'écrase rien sans confirmation.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FICHIER="$RACINE/python/.env"

rouge()  { printf '\033[31m%b\033[0m\n' "$*"; }
vert()   { printf '\033[32m%b\033[0m\n' "$*"; }
jaune()  { printf '\033[33m%b\033[0m\n' "$*"; }
titre()  { printf '\n\033[1m%b\033[0m\n' "$*"; }

titre "Configuration du serveur — Agence Numérique Financière"
echo "Les valeurs demandées viennent de votre tableau de bord Stripe."
echo "Rien n'est affiché à l'écran, rien n'est envoyé nulle part."

# ── Ne jamais écraser une configuration en place sans le dire ──────────────
if [[ -f "$ENV_FICHIER" ]]; then
  jaune "\nUn fichier python/.env existe déjà."
  read -r -p "Le remplacer ? Une sauvegarde sera faite. [o/N] " reponse
  [[ "${reponse,,}" == "o" ]] || { echo "Abandon, rien n'a été modifié."; exit 0; }
  SAUVEGARDE="$ENV_FICHIER.$(date +%Y%m%d-%H%M%S).bak"
  cp -p "$ENV_FICHIER" "$SAUVEGARDE"
  chmod 600 "$SAUVEGARDE"
  vert "Ancienne configuration sauvegardée : $(basename "$SAUVEGARDE")"
fi

# ── Clé secrète Stripe ─────────────────────────────────────────────────────
titre "1/3 — Clé secrète Stripe"
echo "Stripe → Développeurs → Clés API → clé secrète (commence par « sk_ »)."
while :; do
  read -r -s -p "  STRIPE_SECRET_KEY : " STRIPE_SECRET_KEY; echo
  if [[ "$STRIPE_SECRET_KEY" == sk_live_* ]]; then
    vert "  ✔ clé de PRODUCTION"; break
  elif [[ "$STRIPE_SECRET_KEY" == sk_test_* ]]; then
    jaune "  ⚠ clé de TEST : aucun paiement réel ne sera encaissé."
    read -r -p "    Continuer quand même ? [o/N] " r
    [[ "${r,,}" == "o" ]] && break
  else
    rouge "  ✗ une clé secrète Stripe commence par « sk_live_ » ou « sk_test_ »."
    rouge "    (« pk_ » est la clé PUBLIABLE : ce n'est pas celle-ci.)"
  fi
done

# ── Secret de signature du webhook ─────────────────────────────────────────
titre "2/3 — Secret de signature du webhook"
echo "Stripe → Développeurs → Webhooks → votre endpoint → « Signing secret »."
echo "L'endpoint à déclarer est :  https://<votre-domaine>/api/abonnement/webhook"
while :; do
  read -r -s -p "  STRIPE_WEBHOOK_SECRET : " STRIPE_WEBHOOK_SECRET; echo
  if [[ "$STRIPE_WEBHOOK_SECRET" == whsec_* ]]; then
    vert "  ✔ format correct"; break
  fi
  rouge "  ✗ un secret de webhook commence par « whsec_»."
done

# ── Domaine public ─────────────────────────────────────────────────────────
titre "3/3 — Domaine public de l'application"
echo "Exemple : https://agence.mondomaine.fr  (HTTPS obligatoire)"
while :; do
  read -r -p "  Domaine : " DOMAINE
  DOMAINE="${DOMAINE%/}"                     # sans barre oblique finale
  if [[ "$DOMAINE" == https://* && "$DOMAINE" != "https://" ]]; then
    vert "  ✔ $DOMAINE"; break
  fi
  rouge "  ✗ l'adresse doit commencer par « https:// » — le cookie de session"
  rouge "    et les webhooks Stripe ne fonctionnent pas en HTTP simple."
done

# ── Écriture ───────────────────────────────────────────────────────────────
# umask AVANT la création : sans cela le fichier existe brièvement en lecture
# pour tout le monde, et un secret n'a pas besoin de plus d'un instant pour
# être lu sur une machine partagée.
umask 077
cat > "$ENV_FICHIER" <<EOF
# Configuration serveur — généré par scripts/configurer-serveur.sh
# $(date '+%Y-%m-%d %H:%M:%S')
#
# CE FICHIER CONTIENT DES SECRETS. Il est ignoré par git (.gitignore) et exclu
# du paquet distribué (agence.spec). Ne le copiez jamais sur un poste client.

# L'application n'écoute qu'en local ; nginx est seul exposé.
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000

# Stripe — sans ces deux valeurs, AUCUN paiement ne peut être confirmé.
STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET=$STRIPE_WEBHOOK_SECRET

# Serveur public en HTTPS : le cookie de session ne doit jamais circuler
# en clair, et les origines sont restreintes à votre domaine.
AGENCE_COOKIE_SECURE=1
AGENCE_CORS_ORIGINS=$DOMAINE
EOF
chmod 600 "$ENV_FICHIER"

vert "\n✔ $ENV_FICHIER écrit (mode 600)"
ls -l "$ENV_FICHIER" | sed 's/^/  /'

# Propriétaire : le fichier doit appartenir à l'utilisateur qui fait tourner
# le service, sinon systemd ne pourra pas le lire.
if [[ "$(id -u)" -eq 0 ]]; then
  PROPRIETAIRE="$(stat -c '%U' "$RACINE")"
  if [[ "$PROPRIETAIRE" != "root" ]]; then
    chown "$PROPRIETAIRE":"$(stat -c '%G' "$RACINE")" "$ENV_FICHIER"
    vert "  propriétaire ajusté : $PROPRIETAIRE"
  fi
fi

titre "Ce qu'il reste à faire"
cat <<EOF
  1. Liens de paiement de PRODUCTION dans python/licence/config.py
     (les liens livrés sont des liens de test : ils n'encaissent rien).

  2. URL de succès sur chacun de vos deux liens Stripe :
     $DOMAINE/api/abonnement/retour?session_id={CHECKOUT_SESSION_ID}

  3. Webhook Stripe vers :
     $DOMAINE/api/abonnement/webhook
     Événements : checkout.session.completed, invoice.paid,
                  invoice.payment_succeeded, customer.subscription.deleted

  4. Redémarrer le service :   sudo systemctl restart agence

  5. Vérifier :                bash scripts/verifier-serveur.sh $DOMAINE

  Détails complets : DEPLOIEMENT.md
EOF
