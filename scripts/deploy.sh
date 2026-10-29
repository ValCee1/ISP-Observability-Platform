#!/usr/bin/env bash
# Release pipeline, run from your laptop: safety checks -> local config checks
# -> package the exact commit -> Ansible prepares the server, checks again
# against the real files, syncs, reloads/restarts only what changed, verifies.
# Docs: docs/ANSIBLE.md
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$PWD

usage() {
  cat <<'USAGE'
Usage: scripts/deploy.sh --site DIR [options] [REF] [-- ansible-playbook args]

  --site DIR          the ISP's site folder: hosts.ini + group_vars/all/
                      (vars.yml, vault.yml). Default: $ISP_OBS_SITE.
                      Deployed as it is checked out - it must be clean, and
                      pushed when it is a git repo.
  REF                 product commit, tag or branch to deploy (default: HEAD).
                      Roll back by naming an earlier deploy-<isp>-... tag.
  --check             preview what would change; nothing live is touched
  --os-updates        also apt dist-upgrade (reboots only if Ubuntu asks)
  --force-branch      lab servers: allow a product commit that is not on
                      origin/main and a site commit that is not pushed
  --skip-local-checks skip the Docker-based checks on this laptop
                      (the server still runs its own)
  -h, --help          this help

Examples:
  scripts/deploy.sh --site ~/isp-sites/acme --check
  scripts/deploy.sh --site ~/isp-sites/acme
  scripts/deploy.sh --site ~/isp-sites/acme deploy-acme-2026-10-26-0915
  scripts/deploy.sh --site ~/isp-sites/acme -- --ask-become-pass -vv
USAGE
}

die()  { echo "deploy: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

ref=HEAD check=false os_updates=false force_branch=false local_checks=true
site_arg=""
extra=()
while (($#)); do
  case "$1" in
    --site) (($# > 1)) || die "--site needs a folder"; site_arg=$2; shift ;;
    --site=*) site_arg=${1#--site=} ;;
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
. scripts/lib-site.sh
site_resolve "$site_arg"
[[ $site != "$repo/inventories/example" ]] || die "inventories/example is a template - copy it out of the repo and deploy the copy"
[[ -f $site_vault ]] || die "no $site_vault - run: scripts/edit-secrets.sh --site $site"
head -1 "$site_vault" | grep -q '^\$ANSIBLE_VAULT' ||
  die "$site_vault is NOT encrypted. Run: ansible-vault encrypt $site_vault (never commit it like this)"

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

# The site folder is read live by Ansible (group_vars), so what is on disk
# is what ships: it must be committed, and pushed so the history is shared.
site_commit=""
if site_is_git; then
  [[ -z $(git -C "$site" status --porcelain) ]] ||
    die "site $site has uncommitted or untracked files - commit them (Ansible would deploy them as they are)"
  site_commit=$(git -C "$site" rev-parse HEAD)
  if ! $force_branch; then
    git -C "$site" fetch --quiet 2>/dev/null ||
      echo "warning: could not fetch the site repo's remote - checking against what was last fetched"
    [[ -n $(git -C "$site" branch -r --contains "$site_commit" 2>/dev/null) ]] ||
      die "site commit $(git -C "$site" rev-parse --short HEAD) is not pushed - git -C $site push (or --force-branch for a lab server)"
  fi
  echo "site $isp_name = $(git -C "$site" log -1 --format='%h %s')"
else
  echo "warning: site $site is not a git repo - fine for a lab, but keep a real ISP's site in a private repo"
fi

# ---- 2. package the exact commit ------------------------------------------
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -c tar.umask=022 archive --format=tar.gz -o "$tmp/release.tar.gz" "$commit"
mkdir "$tmp/tree" && tar -xzf "$tmp/release.tar.gz" -C "$tmp/tree"

# ---- 3. local checks (on the packaged tree, i.e. what will ship) ----------
step "Local checks"
(cd ansible && ansible-playbook site.yml -i "$site/hosts.ini" --syntax-check >/dev/null) || die "playbook syntax check failed"
echo "ok   playbook syntax"
# The site's targets, topology, exporter config and Alertmanager values,
# rendered into the packaged tree, so the checks below test this ISP's
# files, not the examples. Fails here on a bad inventory (duplicate IPs,
# unknown POPs, missing API logins...).
(cd ansible && ansible-playbook render-site.yml -i "$site/hosts.ini" -e "render_root=$tmp/tree" >"$tmp/render.log" 2>&1) ||
  { sed -n -E '/fatal|ERROR/,$p' "$tmp/render.log" >&2; die "site inventory check failed (above)"; }
echo "ok   site inventory ($isp_name)"
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
  -e "release_ref=$([[ $ref == HEAD ]] && git rev-parse --abbrev-ref HEAD || echo "$ref")"
  -e "site_commit=$site_commit"
  -e "deployed_by=$(git config user.name || whoami)"
  -e "os_updates=$os_updates"
  -i "$site/hosts.ini")
$check && args+=(--check --diff)
(cd ansible && ansible-playbook "${args[@]}" "${extra[@]+"${extra[@]}"}")

# ---- 5. record ------------------------------------------------------------
# Product tag names the ISP (one product repo, many ISPs); the site repo is
# this ISP's alone. Same timestamp in both, so they pair up.
if ! $check; then
  stamp=$(date +%Y-%m-%d-%H%M)
  git tag "deploy-$isp_name-$stamp" "$commit" 2>/dev/null &&
    echo "tagged deploy-$isp_name-$stamp (share it: git push origin deploy-$isp_name-$stamp)"
  if [[ -n $site_commit ]]; then
    git -C "$site" tag "deploy-$stamp" "$site_commit" 2>/dev/null &&
      echo "tagged site deploy-$stamp (share it: git -C $site push origin deploy-$stamp)"
  fi
fi
