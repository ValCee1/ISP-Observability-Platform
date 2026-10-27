#!/usr/bin/env bash
# Release pipeline, run from your laptop: safety checks -> local config checks
# -> package the exact commit -> Ansible prepares the server, checks again
# against the real files, syncs, reloads/restarts only what changed, verifies.
# Docs: docs/ANSIBLE.md
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  cat <<'USAGE'
Usage: scripts/deploy.sh [options] [REF] [-- ansible-playbook args]

  REF                 commit, tag or branch to deploy (default: HEAD).
                      Roll back by naming an earlier deploy-... tag.
  --check             preview what would change; nothing live is touched
  --os-updates        also apt dist-upgrade (reboots only if Ubuntu asks)
  --force-branch      allow a commit that is not on origin/main (lab servers)
  --skip-local-checks skip the Docker-based checks on this laptop
                      (the server still runs its own)
  -h, --help          this help

Examples:
  scripts/deploy.sh --check
  scripts/deploy.sh
  scripts/deploy.sh deploy-2026-10-26-0915
  scripts/deploy.sh -- --ask-become-pass -vv
USAGE
}

die()  { echo "deploy: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

ref=HEAD check=false os_updates=false force_branch=false local_checks=true
extra=()
while (($#)); do
  case "$1" in
    --check) check=true ;;
    --os-updates) os_updates=true ;;
    --force-branch) force_branch=true ;;
    --skip-local-checks) local_checks=false ;;
    -h|--help) usage; exit 0 ;;
    --) shift; extra=("$@"); break ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) ref=$1 ;;
  esac
  shift
done

command -v ansible-playbook >/dev/null || die "ansible not installed (brew install ansible)"
[[ -f ansible/inventory.ini ]] || die "no ansible/inventory.ini - copy ansible/inventory.example.ini and set the server"
[[ -f ansible/secrets.yml ]] || die "no ansible/secrets.yml - run scripts/edit-secrets.sh"
head -1 ansible/secrets.yml | grep -q '^\$ANSIBLE_VAULT' ||
  die "ansible/secrets.yml is NOT encrypted. Run: ansible-vault encrypt ansible/secrets.yml (never commit it like this)"

# ---- 1. safety ------------------------------------------------------------
step "Safety checks"
commit=$(git rev-parse --verify --quiet "$ref^{commit}") || die "unknown ref: $ref"
git diff --quiet HEAD -- ||
  die "uncommitted changes to tracked files - commit or stash them (they would not be deployed, and the playbook runs from this tree)"
if ! $force_branch; then
  git fetch --quiet origin main 2>/dev/null ||
    echo "warning: could not fetch origin - checking against the last known origin/main"
  git merge-base --is-ancestor "$commit" origin/main ||
    die "$ref ($(git rev-parse --short "$commit")) is not on origin/main - merge it first, or --force-branch for a lab server"
fi
echo "deploying $ref = $(git log -1 --format='%h %s' "$commit")"

# ---- 2. package the exact commit ------------------------------------------
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -c tar.umask=022 archive --format=tar.gz -o "$tmp/release.tar.gz" "$commit"
mkdir "$tmp/tree" && tar -xzf "$tmp/release.tar.gz" -C "$tmp/tree"

# ---- 3. local checks (on the packaged tree, i.e. what will ship) ----------
step "Local checks"
(cd ansible && ansible-playbook site.yml --syntax-check >/dev/null) || die "playbook syntax check failed"
echo "ok   playbook syntax"
if $local_checks && docker info >/dev/null 2>&1; then
  prom_image=$(sed -n 's/^ *image: *\(prom\/prometheus:.*\)$/\1/p' "$tmp/tree/docker-compose.yml" | head -1)
  am_image=$(sed -n 's/^ *image: *\(prom\/alertmanager:.*\)$/\1/p' "$tmp/tree/docker-compose.yml" | head -1)
  p="$tmp/tree/prometheus"
  docker run --rm --entrypoint promtool \
    -v "$p/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    -v "$p/rules:/etc/prometheus/rules:ro" -v "$p/targets:/etc/prometheus/targets:ro" \
    "$prom_image" check config /etc/prometheus/prometheus.yml >/dev/null || die "promtool check config failed"
  echo "ok   promtool check config"
  docker run --rm --entrypoint sh -v "$p:/p:ro" "$prom_image" \
    -c 'cd /p && promtool test rules rules/tests/*.yml' >/dev/null || die "promtool test rules failed"
  echo "ok   promtool test rules"
  docker run --rm --entrypoint amtool -v "$tmp/tree/alertmanager:/a:ro" "$am_image" \
    check-config /a/alertmanager.yml >/dev/null || die "amtool check-config failed"
  echo "ok   amtool check-config"
elif $local_checks; then
  echo "warning: Docker is not running - skipping local config checks (the server runs its own)"
fi

# ---- 4. Ansible -----------------------------------------------------------
step "Ansible$($check && echo ' (preview, --check)')"
args=(site.yml
  -e "release_archive=$tmp/release.tar.gz"
  -e "release_commit=$commit"
  -e "release_ref=$ref"
  -e "deployed_by=$(git config user.name || whoami)"
  -e "os_updates=$os_updates")
$check && args+=(--check --diff)
(cd ansible && ansible-playbook "${args[@]}" "${extra[@]+"${extra[@]}"}")

# ---- 5. record ------------------------------------------------------------
if ! $check; then
  tag="deploy-$(date +%Y-%m-%d-%H%M)"
  git tag "$tag" "$commit" 2>/dev/null && echo "tagged $tag (share it: git push origin $tag)"
fi
