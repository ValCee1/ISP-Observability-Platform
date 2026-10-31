#!/usr/bin/env bash
# Prints the Ansible vault password for a site's group_vars/all/vault.yml.
# Ansible calls this (ansible.cfg: vault_password_file), so deploys never
# prompt and the password is never written to a file. Each ISP should have
# its own password. scripts/deploy.sh and edit-secrets.sh export
# ISP_OBS_SITE_ID (the site's isp_name). Sources, first match wins:
#   1. ISP_OBS_VAULT_PASSWORD_FILE - path to a file holding it (CI / Linux)
#   2. the macOS Keychain item "isp-observability-vault-<isp_name>":
#        security add-generic-password -a "$USER" -s isp-observability-vault-acme -w
#   3. the shared Keychain item "isp-observability-vault" (single-ISP setups)
# ISP_OBS_NO_VAULT=1: the site has no vault (the repo's example site, used by
# scripts/render-example.sh); prints a random throwaway value.
set -euo pipefail
service=isp-observability-vault

if [[ ${ISP_OBS_NO_VAULT:-} == 1 ]]; then
  head -c 24 /dev/urandom | base64; exit 0
fi
if [[ -n "${ISP_OBS_VAULT_PASSWORD_FILE:-}" ]]; then
  cat "$ISP_OBS_VAULT_PASSWORD_FILE"; exit 0
fi
if command -v security >/dev/null 2>&1; then
  if [[ -n "${ISP_OBS_SITE_ID:-}" ]] &&
     security find-generic-password -a "$USER" -s "$service-$ISP_OBS_SITE_ID" -w 2>/dev/null; then
    exit 0
  fi
  security find-generic-password -a "$USER" -s "$service" -w 2>/dev/null && exit 0
fi
echo "vault password not found. Store it once with:" >&2
echo "  security add-generic-password -a \"\$USER\" -s $service${ISP_OBS_SITE_ID:+-$ISP_OBS_SITE_ID} -w" >&2
exit 1
