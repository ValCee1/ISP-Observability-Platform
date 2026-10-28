#!/usr/bin/env bash
# Prints the Ansible vault password for ansible/secrets.yml. Ansible calls this
# (ansible.cfg: vault_password_file), so deploys never prompt and the password
# is never written to a file. Sources, first match wins:
#   1. ISP_OBS_VAULT_PASSWORD_FILE - path to a file holding it (CI / Linux)
#   2. the macOS Keychain item "isp-observability-vault" for this user:
#        security add-generic-password -a "$USER" -s isp-observability-vault -w
set -euo pipefail
service=isp-observability-vault

if [[ -n "${ISP_OBS_VAULT_PASSWORD_FILE:-}" ]]; then
  cat "$ISP_OBS_VAULT_PASSWORD_FILE"; exit 0
fi
if command -v security >/dev/null 2>&1 &&
   security find-generic-password -a "$USER" -s "$service" -w 2>/dev/null; then
  exit 0
fi
echo "vault password not found. Store it once with:" >&2
echo "  security add-generic-password -a \"\$USER\" -s $service -w" >&2
exit 1
