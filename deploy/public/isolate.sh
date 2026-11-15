#!/bin/sh
# Keeps the tunnel networks (172.30.0.0/23) away from private address ranges.
# Idempotent: rebuilds its own chain and hooks it into DOCKER-USER and INPUT once.
# Runs before docker.service: Docker keeps an existing DOCKER-USER chain and its rules
# and adds its own FORWARD jump to it on start, so the rules are in place before any
# container exists. Hooking into FORWARD directly would be wrong, because Docker
# inserts its jumps at the top of FORWARD on every start (ACCEPT before our rules).
set -eu

if [ -f /etc/docker/daemon.json ] \
  && grep -q '"firewall-backend"[[:space:]]*:[[:space:]]*"nftables"' /etc/docker/daemon.json; then
  echo "Fehler: Docker nutzt das nftables-Backend, dort gibt es keine DOCKER-USER-Kette." >&2
  exit 1
fi

chain=MDPUBLIC-ISOLATE
src=172.30.0.0/23

iptables -N DOCKER-USER 2>/dev/null || true
iptables -N "$chain" 2>/dev/null || true
iptables -F "$chain"
# cloudflared <-> markitdown-public inside the internal network.
iptables -A "$chain" -d 172.30.1.0/24 -j RETURN
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 \
  0.0.0.0/8 127.0.0.0/8 192.0.0.0/24 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4; do
  iptables -A "$chain" -d "$net" -j DROP
done
iptables -A "$chain" -j RETURN
iptables -C DOCKER-USER -s "$src" -j "$chain" 2>/dev/null \
  || iptables -I DOCKER-USER 1 -s "$src" -j "$chain"
# Traffic to the Pi itself goes through INPUT, not FORWARD. ufw already denies it;
# this makes it independent of the ufw rules. Docker's DNS runs inside the
# container namespace, so nothing legitimate needs the host.
# This also drops replies to connections the host opens into these networks (e.g. curl
# from the host for debugging). That is intended: debug with `docker exec` instead.
iptables -C INPUT -s "$src" -j DROP 2>/dev/null \
  || iptables -I INPUT 1 -s "$src" -j DROP
echo "Isolation aktiv."
