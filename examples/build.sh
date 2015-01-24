#!/bin/sh
for CAPTURE in "$@"; do
  CAPTURE_BASE="$(basename "$CAPTURE")"
  echo -n "$CAPTURE"
  iti1480a-display -i "$CAPTURE" | aha -b > "${CAPTURE_BASE}.html"
  for VERBOSITY in q v vv vvv vvvv; do
    echo -n .
    iti1480a-display "-$VERBOSITY" -i "$CAPTURE" | aha -b > "${CAPTURE_BASE}_${VERBOSITY}.html"
  done
  echo
done
