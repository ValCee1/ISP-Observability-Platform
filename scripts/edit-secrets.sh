#!/usr/bin/env bash
# Opens a site's vault (<site>/group_vars/all/vault.yml: the ISP's passwords,
# tokens, API logins, SNMP credentials) in your editor; it is re-encrypted on
# save. First run: creates it from the site's vault.example.yml (or the
# product's inventories/example/vault.example.yml) and encrypts it before you
# type anything. The vault password comes from the macOS Keychain
# (ansible/vault-pass.sh): item isp-observability-vault-<isp_name>, else
# isp-observability-vault.
#   scripts/edit-secrets.sh --site ~/isp-sites/acme
# Docs: docs/ANSIBLE.md
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$PWD
die() { echo "edit-secrets: $*" >&2; exit 1; }

site_arg=""
while (($#)); do
  case "$1" in
    --site) (($# > 1)) || die "--site needs a folder"; site_arg=$2; shift ;;
    --site=*) site_arg=${1#--site=} ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument $1 (usage: scripts/edit-secrets.sh --site <folder>)" ;;
  esac
  shift
done
. scripts/lib-site.sh
site_resolve "$site_arg"
[[ $site != "$repo/inventories/example" ]] || die "inventories/example is a template - copy it out of the repo first"
cd ansible   # ansible.cfg: vault_password_file

if [[ ! -f $site_vault ]]; then
  template=$site/vault.example.yml
  [[ -f $template ]] || template=$repo/inventories/example/vault.example.yml
  cp "$template" "$site_vault"
  ansible-vault encrypt "$site_vault"
  echo "Created and encrypted $site_vault from $template."
fi
head -1 "$site_vault" | grep -q '^\$ANSIBLE_VAULT' ||
  die "$site_vault is not encrypted - run: ansible-vault encrypt $site_vault"
ansible-vault edit "$site_vault"
echo "Saved (encrypted). Commit it in the site repo, then scripts/deploy.sh --site $site."
