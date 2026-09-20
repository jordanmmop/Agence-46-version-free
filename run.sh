#!/usr/bin/env bash
# Utilise python3 si présent, sinon python — sans masquer les erreurs
# du script lui-même (un traceback de start.py doit rester visible).
if command -v python3 >/dev/null 2>&1; then
  exec python3 start.py "$@"
else
  exec python start.py "$@"
fi
