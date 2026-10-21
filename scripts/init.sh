#!/usr/bin/env bash
# First-install helper: creates every per-site file the stack needs but git
# does not contain, from the repo's examples/placeholders. NEVER overwrites an
# existing file, so it is safe to re-run. Afterwards edit the files it lists
# (docs/SETUP-INPUTS.md says what goes in each).
set -euo pipefail
cd "$(dirname "$0")/.."

made=()
mk() { # mk <dest> <source-or-literal-text> [mode]
  [[ -e "$1" ]] && return 0
  mkdir -p "$(dirname "$1")"
  if [[ -f "$2" ]]; then cp "$2" "$1"; else printf '%s' "$2" > "$1"; fi
  chmod "${3:-600}" "$1"; made+=("$1")
}

# Secrets: compose refuses to start if a secret file is missing, so create
# obvious placeholders. grafana's is a real (weak) default - CHANGE IT.
mk secrets/grafana_admin_password.txt   "change-me-now"
mk secrets/telegram_bot_token.txt       "PUT-TELEGRAM-BOT-TOKEN-HERE"
mk secrets/whatsapp_access_token.txt    "dry-run-placeholder"
mk secrets/routeros_api_credentials.json routeros_exporter/routeros_api_credentials.example.json
mk routeros_exporter/config.yml         routeros_exporter/config.example.yml 644
mk .env                                 .env.example 600

if ((${#made[@]})); then
  echo "Created (edit these - see docs/SETUP-INPUTS.md):"; printf '  %s\n' "${made[@]}"
else
  echo "Nothing to create - all per-site files already exist."
fi
