#!/bin/sh
# Reads an Anthropic API key from stdin and stores it in .env (root only).
set -eu
dir=/opt/markitdown-web
IFS= read -r key || true
# Whitelist the characters of a key; this also drops BOM, CR, tabs and spaces.
key=$(printf '%s' "$key" | LC_ALL=C tr -cd 'A-Za-z0-9_-')
case "$key" in
  sk-ant-?*) ;;
  *) echo "Das sieht nicht nach einem Anthropic-Key aus." >&2; exit 1 ;;
esac
umask 077
tmp=$(mktemp "$dir/.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf 'ANTHROPIC_API_KEY=%s\n' "$key" > "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$dir/.env"
echo "Key gespeichert."
