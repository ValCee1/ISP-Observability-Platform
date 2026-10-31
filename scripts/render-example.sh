#!/usr/bin/env bash
# Re-renders the repo's example per-site files from the example site
# (inventories/example): prometheus/targets/*.yml, prometheus/topology.yml,
# routeros_exporter/config.example.yml and the site values in
# alertmanager/alertmanager.yml and prometheus/rules/sector-normalization.yml. Same templates as a real deploy, so the
# local checks, promtool tests and the Docker image always ship valid data.
#   scripts/render-example.sh          rewrite the files
#   scripts/render-example.sh --check  only report whether they are current
# Docs: docs/ANSIBLE.md
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$PWD
files=(prometheus/targets/mikrotik-routers.yml prometheus/targets/mikrotik-sectors.yml
       prometheus/targets/links.yml prometheus/targets/vantage-points.yml
       prometheus/topology.yml routeros_exporter/config.example.yml
       alertmanager/alertmanager.yml prometheus/rules/sector-normalization.yml)

out=$(mktemp -d); trap 'rm -rf "$out"' EXIT
for f in "${files[@]}"; do mkdir -p "$out/$(dirname "$f")"; cp "$f" "$out/$f"; done

# The example site has no vault (only vault.example.yml): ISP_OBS_NO_VAULT
# tells ansible/vault-pass.sh not to ask the Keychain.
(cd ansible && ISP_OBS_NO_VAULT=1 ansible-playbook render-site.yml \
  -i "$repo/inventories/example/hosts.ini" \
  -e "@$repo/inventories/example/vault.example.yml" \
  -e "render_root=$out" -e routeros_config_path=routeros_exporter/config.example.yml \
  >/dev/null) || { echo "render-example: rendering failed - rerun with: cd ansible && ansible-playbook render-site.yml -i ../inventories/example/hosts.ini -e @../inventories/example/vault.example.yml -e render_root=/tmp/x" >&2; exit 1; }

stale=()
for f in "${files[@]}"; do cmp -s "$out/$f" "$f" || stale+=("$f"); done
if [[ ${1:-} == --check ]]; then
  ((${#stale[@]} == 0)) && { echo "ok   example files match inventories/example"; exit 0; }
  echo "render-example: out of date (run scripts/render-example.sh): ${stale[*]}" >&2; exit 1
fi
for f in "${stale[@]+"${stale[@]}"}"; do cp "$out/$f" "$f"; echo "updated $f"; done
((${#stale[@]})) || echo "example files already current"
