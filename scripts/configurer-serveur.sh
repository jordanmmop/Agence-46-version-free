#!/usr/bin/env bash
#
# Génère python/.env pour un déploiement serveur (voir DEPLOIEMENT.md).
#
# À lancer SUR LE SERVEUR, depuis le dossier de l'application :
#
#     bash scripts/configurer-serveur.sh            configuration complète
#     bash scripts/configurer-serveur.sh --envoi    e-mail / SMS UNIQUEMENT
#
# Le mode « --envoi » ne touche QU'aux lignes d'envoi : un serveur déjà
# configuré n'a pas à ressaisir ses clés Stripe ni son mot de passe de base
# pour ajouter la remise des clés d'abonnement.
#
# Le script demande les valeurs, les contrôle, écrit le fichier en mode 600 et
# n'affiche JAMAIS les secrets saisis. Il n'écrase rien sans confirmation.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FICHIER="$RACINE/python/.env"

MODE="complet"
case "${1:-}" in
  --envoi|--email|--smtp) MODE="envoi" ;;
  --aide|-h|--help)
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  "") ;;
  *) printf 'Option inconnue : %s (voir --aide)\n' "$1" >&2; exit 2 ;;
esac

rouge()  { printf '\033[31m%b\033[0m\n' "$*"; }
vert()   { printf '\033[32m%b\033[0m\n' "$*"; }
jaune()  { printf '\033[33m%b\033[0m\n' "$*"; }
titre()  { printf '\n\033[1m%b\033[0m\n' "$*"; }

# ── Écriture ciblée d'une variable ────────────────────────────────────────
# Remplace la ligne « NOM=… » si elle existe, l'ajoute sinon. C'est ce qui
# permet d'ajouter l'envoi à une configuration en place sans rien perdre :
# réécrire tout le fichier obligerait à ressaisir des secrets déjà posés.
definir_variable() {
  local nom="$1" valeur="$2"
  touch "$ENV_FICHIER"; chmod 600 "$ENV_FICHIER"
  if grep -qE "^[[:space:]]*#?[[:space:]]*${nom}=" "$ENV_FICHIER"; then
    # Le remplacement passe par un fichier temporaire du même dossier, créé
    # sous umask 077 : jamais par « sed -i », qui laisse un instant un fichier
    # lisible par tous.
    local tmp; tmp="$(mktemp "$ENV_FICHIER.XXXXXX")"
    awk -v nom="$nom" -v val="$valeur" '
      $0 ~ "^[[:space:]]*#?[[:space:]]*" nom "=" && !fait { print nom "=" val; fait=1; next }
      { print }' "$ENV_FICHIER" > "$tmp"
    chmod 600 "$tmp"; mv "$tmp" "$ENV_FICHIER"
  else
    printf '%s=%s\n' "$nom" "$valeur" >> "$ENV_FICHIER"
  fi
}

# ── Questionnaire « remise des clés d'abonnement » ────────────────────────
demander_envoi() {
  local prefixe="$1"

  titre "${prefixe} — Envoi de la clé d'abonnement par e-mail"
  cat <<'TXT'
Quand un paiement est confirmé, l'application émet une clé d'abonnement
(AGF-XXXXX-XXXXX-XXXXX) et l'envoie au client. Sans serveur d'envoi, la clé
reste affichée à l'écran mais ne part nulle part.

Chez OVH : ssl0.ovh.net, port 587 (STARTTLS) ou 465 (SSL), identifiant =
l'adresse e-mail complète. Laissez l'hôte vide pour ne pas configurer l'envoi.
TXT
  read -r -p "  Serveur SMTP (vide = pas d'envoi) : " SMTP_HOTE
  if [[ -z "$SMTP_HOTE" ]]; then
    jaune "  aucun envoi configuré — la clé restera affichée à l'écran"
    return 0
  fi

  read -r -p "  Sécurité [starttls/ssl/aucune] (starttls) : " SMTP_SECURITE
  SMTP_SECURITE="${SMTP_SECURITE:-starttls}"
  case "$SMTP_SECURITE" in
    starttls|ssl|aucune) ;;
    *) jaune "  valeur inconnue — « starttls » retenu"; SMTP_SECURITE="starttls" ;;
  esac
  [[ "$SMTP_SECURITE" == "ssl" ]] && DEFAUT_PORT=465 || DEFAUT_PORT=587

  while :; do
    read -r -p "  Port [$DEFAUT_PORT] : " SMTP_PORT
    SMTP_PORT="${SMTP_PORT:-$DEFAUT_PORT}"
    [[ "$SMTP_PORT" =~ ^[0-9]+$ ]] && break
    rouge "  ✗ le port doit être un nombre."
  done

  while :; do
    read -r -p "  Adresse expéditrice (ex. no-reply@mondomaine.fr) : " SMTP_EXPEDITEUR
    [[ "$SMTP_EXPEDITEUR" == *@*.* ]] && break
    rouge "  ✗ une adresse e-mail complète est attendue."
  done

  read -r -p "  Identifiant SMTP [$SMTP_EXPEDITEUR] : " SMTP_UTILISATEUR
  SMTP_UTILISATEUR="${SMTP_UTILISATEUR:-$SMTP_EXPEDITEUR}"
  read -r -s -p "  Mot de passe SMTP : " SMTP_MOTDEPASSE; echo
  [[ -z "$SMTP_MOTDEPASSE" ]] && jaune "  ⚠ mot de passe vide : seuls certains relais internes l'acceptent."
  ENVOI_EMAIL=1
  vert "  ✔ envoi par e-mail via $SMTP_HOTE:$SMTP_PORT"

  # ── SMS : facultatif, l'e-mail suffit à remettre la clé ──
  titre "${prefixe} bis — Envoi par SMS (facultatif)"
  echo "Passerelles reconnues : ovh, twilio, http. Laissez vide pour ignorer."
  read -r -p "  Passerelle SMS (vide = aucune) : " SMS_FOURNISSEUR
  case "${SMS_FOURNISSEUR,,}" in
    ovh)
      echo "  Identifiants : https://eu.api.ovh.com/createToken"
      echo "  Droits requis : GET /sms/* et POST /sms/*"
      read -r -p "    OVH_APPLICATION_KEY : " OVH_APPLICATION_KEY
      read -r -s -p "    OVH_APPLICATION_SECRET : " OVH_APPLICATION_SECRET; echo
      read -r -s -p "    OVH_CONSUMER_KEY : " OVH_CONSUMER_KEY; echo
      read -r -p "    Service SMS (ex. sms-xx99999-1) : " OVH_SERVICE_SMS
      read -r -p "    Expéditeur affiché (11 car. max) : " OVH_EXPEDITEUR
      ENVOI_SMS="ovh"; vert "  ✔ SMS par OVHcloud" ;;
    twilio)
      read -r -p "    TWILIO_ACCOUNT_SID : " TWILIO_ACCOUNT_SID
      read -r -s -p "    TWILIO_AUTH_TOKEN : " TWILIO_AUTH_TOKEN; echo
      read -r -p "    Numéro expéditeur (+33…) : " TWILIO_EXPEDITEUR
      ENVOI_SMS="twilio"; vert "  ✔ SMS par Twilio" ;;
    http)
      while :; do
        read -r -p "    URL de la passerelle (https://…) : " SMS_URL
        [[ "$SMS_URL" == https://* ]] && break
        rouge "    ✗ HTTPS obligatoire : le message contient la clé."
      done
      read -r -s -p "    En-tête Authorization (vide si aucun) : " SMS_AUTORISATION; echo
      ENVOI_SMS="http"; vert "  ✔ SMS par passerelle HTTP" ;;
    "") jaune "  pas de SMS — l'e-mail suffit à remettre la clé" ;;
    *)  jaune "  passerelle inconnue — SMS ignoré" ;;
  esac
}

# ── Report des réponses d'envoi dans le fichier ───────────────────────────
ecrire_envoi() {
  [[ "${ENVOI_EMAIL:-0}" == "1" ]] || return 0
  definir_variable SMTP_HOTE       "$SMTP_HOTE"
  definir_variable SMTP_PORT       "$SMTP_PORT"
  definir_variable SMTP_SECURITE   "$SMTP_SECURITE"
  definir_variable SMTP_UTILISATEUR "$SMTP_UTILISATEUR"
  definir_variable SMTP_MOTDEPASSE "$SMTP_MOTDEPASSE"
  definir_variable SMTP_EXPEDITEUR "$SMTP_EXPEDITEUR"
  case "${ENVOI_SMS:-}" in
    ovh)
      definir_variable SMS_FOURNISSEUR       "ovh"
      definir_variable OVH_APPLICATION_KEY    "$OVH_APPLICATION_KEY"
      definir_variable OVH_APPLICATION_SECRET "$OVH_APPLICATION_SECRET"
      definir_variable OVH_CONSUMER_KEY       "$OVH_CONSUMER_KEY"
      definir_variable OVH_SERVICE_SMS        "$OVH_SERVICE_SMS"
      [[ -n "${OVH_EXPEDITEUR:-}" ]] && definir_variable OVH_EXPEDITEUR "$OVH_EXPEDITEUR" ;;
    twilio)
      definir_variable SMS_FOURNISSEUR    "twilio"
      definir_variable TWILIO_ACCOUNT_SID "$TWILIO_ACCOUNT_SID"
      definir_variable TWILIO_AUTH_TOKEN  "$TWILIO_AUTH_TOKEN"
      definir_variable TWILIO_EXPEDITEUR  "$TWILIO_EXPEDITEUR" ;;
    http)
      definir_variable SMS_FOURNISSEUR "http"
      definir_variable SMS_URL         "$SMS_URL"
      [[ -n "${SMS_AUTORISATION:-}" ]] && definir_variable SMS_AUTORISATION "$SMS_AUTORISATION" ;;
  esac
}

# ── Vérification RÉELLE : configurer n'est pas envoyer ────────────────────
# Un mot de passe refusé, un port filtré par l'hébergeur : cela ne se voit
# qu'à l'essai. Mieux vaut le découvrir maintenant que le jour où un client
# paie.
tester_envoi() {
  [[ "${ENVOI_EMAIL:-0}" == "1" ]] || return 0
  local python="$RACINE/../venv/bin/python"
  [[ -x "$python" ]] || python="$(command -v python3 || true)"
  [[ -x "$python" ]] || { jaune "\n  (Python introuvable : test d'envoi ignoré)"; return 0; }

  titre "Test d'envoi"
  read -r -p "  Envoyer un e-mail de test ? Adresse (vide = ignorer) : " DESTINATAIRE
  [[ -z "$DESTINATAIRE" ]] && { jaune "  test ignoré"; return 0; }

  ( cd "$RACINE" && set -a && . "$ENV_FICHIER" && set +a &&
    PYTHONPATH="$RACINE/python" "$python" - "$DESTINATAIRE" <<'PY'
import sys
from licence import notifications
ok, detail = notifications.envoyer_email(
    sys.argv[1], "Test — Agence Numérique Financière",
    "Cet e-mail confirme que la remise des cles d'abonnement fonctionne.\n"
    "Aucune cle reelle n'est contenue dans ce message.\n")
print(("  OK  " if ok else "  ECHEC  ") + detail)
sys.exit(0 if ok else 1)
PY
  ) && vert "  ✔ e-mail parti — vérifiez la boîte de réception" \
    || rouge "  ✗ l'envoi a échoué : corrigez les valeurs et relancez --envoi"
}

titre "Configuration du serveur — Agence Numérique Financière"
echo "Les valeurs demandées viennent de votre tableau de bord Stripe."
echo "Rien n'est affiché à l'écran, rien n'est envoyé nulle part."

# ── Mode « envoi seul » : on complète, on ne remplace pas ──────────────────
if [[ "$MODE" == "envoi" ]]; then
  if [[ ! -f "$ENV_FICHIER" ]]; then
    rouge "python/.env est absent : lancez d'abord la configuration complète"
    rouge "    bash scripts/configurer-serveur.sh"
    exit 2
  fi
  SAUVEGARDE="$ENV_FICHIER.$(date +%Y%m%d-%H%M%S).bak"
  cp -p "$ENV_FICHIER" "$SAUVEGARDE"; chmod 600 "$SAUVEGARDE"
  vert "\nSauvegarde : $(basename "$SAUVEGARDE") — le reste du fichier est conservé."

  umask 077
  demander_envoi "1/1"
  ecrire_envoi
  chmod 600 "$ENV_FICHIER"
  vert "\n✔ $ENV_FICHIER mis à jour (mode 600)"
  tester_envoi
  titre "Ce qu'il reste à faire"
  cat <<EOF
  1. Redémarrer le service :  sudo systemctl restart agence
  2. Contrôler :              python scripts/abonnement.py canaux
EOF
  exit 0
fi

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
titre "1/5 — Clé secrète Stripe"
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
titre "2/5 — Secret de signature du webhook"
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
titre "3/5 — Domaine public de l'application"
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

# ── Base des comptes (optionnelle) ─────────────────────────────────────────
titre "4/5 — Base des comptes (facultatif)"
echo "Par défaut, les comptes vivent dans la base SQLite de CETTE machine."
echo "Pour les centraliser sur un serveur PostgreSQL, indiquez-le ici."
echo "Laissez vide pour garder le stockage local."
DSN_COMPTES=""
while :; do
  read -r -p "  Hôte PostgreSQL (vide = stockage local) : " PG_HOTE
  [[ -z "$PG_HOTE" ]] && { jaune "  stockage local conservé"; break; }

  # Une adresse IPv6 DOIT être entre crochets : sans eux, les deux-points de
  # l'adresse sont lus comme le séparateur du port. On les ajoute nous-mêmes
  # plutôt que de laisser l'utilisateur buter dessus.
  if [[ "$PG_HOTE" != \[* && "$(tr -cd ':' <<<"$PG_HOTE" | wc -c)" -gt 1 ]]; then
    PG_HOTE="[$PG_HOTE]"
    jaune "  adresse IPv6 détectée — crochets ajoutés : $PG_HOTE"
  fi

  read -r -p "  Port [5432] : " PG_PORT; PG_PORT="${PG_PORT:-5432}"
  read -r -p "  Base [agence] : " PG_BASE; PG_BASE="${PG_BASE:-agence}"
  read -r -p "  Utilisateur [agence] : " PG_USER; PG_USER="${PG_USER:-agence}"
  read -r -s -p "  Mot de passe : " PG_MDP; echo
  [[ -z "$PG_MDP" ]] && { rouge "  ✗ mot de passe requis."; continue; }

  # Encodage minimal du mot de passe : « @ » et « / » couperaient le DSN en deux.
  PG_MDP_ENC="$(printf '%s' "$PG_MDP" | sed -e 's/%/%25/g' -e 's/@/%40/g' \
                   -e 's|/|%2F|g' -e 's/:/%3A/g' -e 's/?/%3F/g' -e 's/#/%23/g')"
  DSN_COMPTES="postgresql://${PG_USER}:${PG_MDP_ENC}@${PG_HOTE}:${PG_PORT}/${PG_BASE}?sslmode=require"
  vert "  ✔ comptes centralisés sur ${PG_HOTE}:${PG_PORT}/${PG_BASE} (TLS exigé)"
  break
done

# ── Remise des clés d'abonnement ───────────────────────────────────────────
demander_envoi "5/5"

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

if [[ -n "$DSN_COMPTES" ]]; then
  cat >> "$ENV_FICHIER" <<EOF

# Comptes centralisés sur une base PostgreSQL. Cette ligne contient un mot de
# passe : d'où le mode 600 de ce fichier.
AGENCE_COMPTES_DSN=$DSN_COMPTES
EOF
fi
ecrire_envoi
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

tester_envoi

titre "Ce qu'il reste à faire"
cat <<EOF
  1. Vérifier les liens de paiement (python/licence/config.py, ou les
     variables STRIPE_LIEN_MENSUEL / STRIPE_LIEN_ANNUEL). Les liens livrés
     sont ceux de PRODUCTION : ils encaissent des règlements réels.

  2. URL de succès sur chacun de vos deux liens Stripe :
     $DOMAINE/api/abonnement/retour?session_id={CHECKOUT_SESSION_ID}

  3. Webhook Stripe vers :
     $DOMAINE/api/abonnement/webhook
     Événements : checkout.session.completed, invoice.paid,
                  invoice.payment_succeeded, customer.subscription.deleted

  4. Si vous avez centralisé les comptes, installer le pilote :
     pip install -r python/requirements-serveur.txt

  5. Redémarrer le service :   sudo systemctl restart agence

  6. Vérifier :                bash scripts/verifier-serveur.sh $DOMAINE
                               python scripts/abonnement.py canaux

  Pour ajouter ou corriger l'envoi plus tard, sans rien ressaisir d'autre :
     bash scripts/configurer-serveur.sh --envoi

  Détails complets : DEPLOIEMENT.md
EOF
