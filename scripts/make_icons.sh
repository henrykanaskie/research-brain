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

# The browser extension's icons (extension/icons/): the same mark on the same ground, in a rounded square
# like the Mac app icon. Small toolbar sizes get thicker lines (x$4) so the brain still reads at 16 px.
mkext() {  # size scale file stroke-multiplier
  thick=$(printf '%s' "$inner" | awk -v m="$4" '{
    while (match($0, /stroke-width="[0-9.]+"/)) {
      w = substr($0, RSTART + 14, RLENGTH - 15) * m
      printf "%s", substr($0, 1, RSTART - 1) "stroke-width=\"" w "\""
      $0 = substr($0, RSTART + RLENGTH)
    }
    print
  }')
  base=$(awk -v m="$4" 'BEGIN { print 1.6 * m }')
  cat > /tmp/rb-ext.svg <<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="$1" height="$1">
  <rect x="3" y="3" width="94" height="94" rx="22" fill="#EEEFEB" stroke="#D5D7D1" stroke-width="2"/>
  <g transform="translate(50 50) scale($2) translate(-19.5 -17)" fill="none" stroke="#15181B" stroke-width="$base" stroke-linecap="round" stroke-linejoin="round">
$thick
  </g>
</svg>
SVG
  rsvg-convert -w "$1" -h "$1" /tmp/rb-ext.svg -o "extension/icons/$3"
}
mkext 128 2.15 icon128.png 1
mkext 48 2.2 icon48.png 1.15
mkext 32 2.3 icon32.png 1.35
mkext 16 2.35 icon16.png 1.7
rm -f /tmp/rb-ext.svg
