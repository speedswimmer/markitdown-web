#!/bin/sh
# Reads a Cloudflare Tunnel token from stdin and stores it in .env (root only).
set -eu
dir=/opt/markitdown-public
IFS= read -r token || true
# Tokens are base64; the whitelist also drops BOM, CR, tabs and spaces.
token=$(printf '%s' "$token" | LC_ALL=C tr -cd 'A-Za-z0-9+/=_-')
case "$token" in
  eyJ?*) ;;
  *) echo "Das sieht nicht nach einem Tunnel-Token aus." >&2; exit 1 ;;
esac
if [ "${#token}" -lt 100 ]; then
  echo "Token zu kurz, wurde er vollständig kopiert?" >&2
  exit 1
fi
umask 077
tmp=$(mktemp "$dir/.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf 'TUNNEL_TOKEN=%s\n' "$token" > "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$dir/.env"
echo "Token gespeichert."
