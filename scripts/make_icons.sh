#!/bin/sh
# Builds the web-app icons in static/icons/ from static/brain.svg (needs rsvg-convert, from librsvg).
# The mark sits on the vellum ground; the maskable icon keeps it inside the central 60% safe zone.
set -e
cd "$(dirname "$0")/.."
inner=$(sed -n '/<svg/,/<\/svg>/p' static/brain.svg | sed '1d;$d')
mk() {  # size scale file
  cat > /tmp/rb-icon.svg <<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="$1" height="$1">
  <rect width="100" height="100" fill="#EEEFEB"/>
  <g transform="translate(50 50) scale($2) translate(-20 -17)" fill="none" stroke="#15181B" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
$inner
  </g>
</svg>
SVG
  rsvg-convert -w "$1" -h "$1" /tmp/rb-icon.svg -o "static/icons/$3"
}
mkdir -p static/icons
mk 192 1.9 icon-192.png
mk 512 1.9 icon-512.png
mk 512 1.45 icon-maskable-512.png
mk 180 1.9 apple-touch-icon.png
rm -f /tmp/rb-icon.svg
