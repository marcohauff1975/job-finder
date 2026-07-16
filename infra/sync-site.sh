#!/usr/bin/env bash
# Sync the req2prod.nl static site from the repo checkout to the web root.
#
# nginx serves req2prod.nl from /var/www/req2prod.nl, not from the checkout, so
# `git pull` alone does not update the served site. Both deploy workflows call
# this immediately after pulling.
#
# Usage: sudo infra/sync-site.sh <repo-dir>
set -euo pipefail

REPO_DIR="${1:?usage: sync-site.sh <repo-dir>}"
SRC="$REPO_DIR/site"
DEST="${DEST_OVERRIDE:-/var/www/req2prod.nl}"

# rsync --delete mirrors the source. Against a missing source, or one with no
# publishable content (only dotfiles and/or empty directories), it would wipe
# the live site, so refuse rather than publish nothing.
if [ ! -d "$SRC" ]; then
  echo "refusing to sync: $SRC does not exist" >&2
  exit 1
fi
if [ -z "$(find "$SRC" -type f -not -path '*/.*' -print -quit)" ]; then
  echo "refusing to sync: $SRC has nothing publishable (--delete would wipe $DEST)" >&2
  exit 1
fi

mkdir -p "$DEST"
rsync -a --delete "$SRC/" "$DEST/"
echo "synced $SRC -> $DEST"
