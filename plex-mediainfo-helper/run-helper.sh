#!/bin/zsh
set -euo pipefail
umask 077

keychain_account="ranzhi"
keychain_service="com.ranzhi.plex-mediainfo-helper"
plex_keychain_service="com.ranzhi.plex-mediainfo-helper.plex-token"
helper_token=$(/usr/bin/security find-generic-password \
  -a "$keychain_account" \
  -s "$keychain_service" \
  -w 2>/dev/null || true)

if [[ -z "$helper_token" ]]; then
  print -u2 "Plex MediaInfo Helper token is not available in Keychain"
  exit 1
fi

export PTH_TOKEN="$helper_token"
plex_token=$(/usr/bin/security find-generic-password \
  -a "$keychain_account" \
  -s "$plex_keychain_service" \
  -w 2>/dev/null || true)
if [[ -n "$plex_token" ]]; then
  export PTH_PLEX_TOKEN="$plex_token"
fi

exec /usr/bin/python3 \
  "/Users/ranzhi/Projects/p115-directory-pr/plex-mediainfo-helper/plex_mediainfo_helper.py"
