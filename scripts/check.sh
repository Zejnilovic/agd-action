#!/usr/bin/env bash
# Runs agd status --check and sets the due output. An update being due never fails the step.
set -euo pipefail

case "${MODE:-}" in
  pull-request | check) ;;
  *) echo "::error::mode must be pull-request or check, not '${MODE:-}'."; exit 1 ;;
esac

code=0
agd status --check || code=$?
# 0: nothing due, 1: an update is due, anything else: an error.
[ "$code" -le 1 ] || exit "$code"
if [ "$code" -eq 1 ]; then due=true; else due=false; fi
echo "due=$due" >> "$GITHUB_OUTPUT"
