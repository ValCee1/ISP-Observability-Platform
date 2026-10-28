#!/usr/bin/env bash
# Opens ansible/secrets.yml (the encrypted per-site settings and secrets) in
# your editor; it is re-encrypted on save. First run: creates it from
# ansible/secrets.example.yml and encrypts it before you type anything.
# The vault password comes from the macOS Keychain (ansible/vault-pass.sh).
# Docs: docs/ANSIBLE.md
set -euo pipefail
cd "$(dirname "$0")/../ansible"

if [[ ! -f secrets.yml ]]; then
  cp secrets.example.yml secrets.yml
  ansible-vault encrypt secrets.yml
  echo "Created and encrypted ansible/secrets.yml from the template."
fi
head -1 secrets.yml | grep -q '^\$ANSIBLE_VAULT' ||
  { echo "ansible/secrets.yml is not encrypted - run: ansible-vault encrypt ansible/secrets.yml" >&2; exit 1; }
ansible-vault edit secrets.yml
echo "Saved (encrypted). Commit it, then scripts/deploy.sh."
