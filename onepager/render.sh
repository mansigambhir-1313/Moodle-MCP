#!/usr/bin/env bash
# Render a report HTML to a one-page A4 PDF with headless Chrome.
# Usage: render.sh report.html [out.pdf]
set -euo pipefail
IN="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="${2:-${IN%.html}.pdf}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --no-margins \
  --virtual-time-budget=20000 --force-color-profile=srgb \
  --print-to-pdf="$OUT" "file://$IN" 2>/dev/null
echo "PDF: $OUT"
