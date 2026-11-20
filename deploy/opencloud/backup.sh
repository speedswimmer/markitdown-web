#!/bin/sh
# Nightly restic backup of OpenCloud to an external USB disk mounted at /mnt/backup.
# OpenCloud is stopped for the duration so config and data are consistent.
# Repository password: /opt/opencloud/restic.pw (root only) - keep a copy elsewhere,
# without it the backup is unreadable.
set -eu

dir=/opt/opencloud
mount=/mnt/backup
export RESTIC_REPOSITORY="$mount/opencloud-restic"
export RESTIC_PASSWORD_FILE="$dir/restic.pw"

if ! mountpoint -q "$mount"; then
  echo "Fehler: $mount ist nicht eingehängt, kein Backup." >&2
  exit 1
fi

# First run: create the repository.
restic cat config >/dev/null 2>&1 || restic init

cd "$dir"
docker compose stop opencloud
# Always restart OpenCloud, even if restic fails.
trap 'docker compose start opencloud' EXIT

restic backup --tag opencloud "$dir/config" "$dir/data" "$dir/compose.yaml" "$dir/.env"
restic forget --tag opencloud --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
restic check --read-data-subset=5%
