# Shared by scripts/deploy.sh and scripts/edit-secrets.sh: resolve and check
# the per-ISP site folder (--site, or ISP_OBS_SITE). Sourced, not run.
# Layout: <site>/hosts.ini, <site>/group_vars/all/{vars.yml,vault.yml}.
# Docs: docs/ANSIBLE.md

# site_resolve <path>: sets site (absolute), site_vars, site_vault, isp_name,
# and exports ISP_OBS_SITE_ID so ansible/vault-pass.sh picks this site's
# Keychain item.
site_resolve() {
  local p=${1:-${ISP_OBS_SITE:-}}
  if [[ -z $p ]]; then
    [[ ! -f $repo/ansible/secrets.yml ]] ||
      die "ansible/secrets.yml is the old single-site layout - move it into a site folder (docs/ANSIBLE.md, \"Moving from ansible/secrets.yml\"), then pass --site"
    die "no site folder - pass --site <path-to-isp-folder> (or set ISP_OBS_SITE); a template is inventories/example"
  fi
  [[ -d $p ]] || die "site folder not found: $p"
  site=$(cd "$p" && pwd)
  [[ $site/ != "$repo"/* ]] || [[ $site == "$repo/inventories/example" ]] ||
    die "the site folder must live outside the product repo (its own private git repo): $site"
  site_vars=$site/group_vars/all/vars.yml
  site_vault=$site/group_vars/all/vault.yml
  [[ -f $site/hosts.ini ]] || die "$site has no hosts.ini (copy inventories/example)"
  [[ -f $site_vars ]] || die "$site has no group_vars/all/vars.yml (copy inventories/example)"
  isp_name=$(sed -n 's/^isp_name:[[:space:]]*["'\'']\{0,1\}\([a-z0-9][a-z0-9-]*\).*/\1/p' "$site_vars" | head -1)
  [[ -n $isp_name ]] || die "$site_vars: set isp_name (lowercase letters, digits, dashes)"
  export ISP_OBS_SITE_ID=$isp_name
}

# Is the site folder a git repo (its own, not the product's)?
site_is_git() {
  [[ $(git -C "$site" rev-parse --show-toplevel 2>/dev/null) == "$site" ]]
}
