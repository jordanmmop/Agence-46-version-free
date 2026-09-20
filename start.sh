#!/usr/bin/env bash
# Délégation vers le point d'entrée RÉEL. Ce lanceur ouvrait auparavant une
# interface de démonstration (positions et performances générées
# aléatoirement) : elle a été supprimée pour ne jamais afficher de chiffres
# inventés dans une application qui engage de l'argent réel.
set -e
cd "$(cd "$(dirname "$0")" && pwd)"
exec "$(command -v python3 || command -v python)" start.py
