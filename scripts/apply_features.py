#!/usr/bin/env python3
"""Turn features.yml + feature-manifest.yml into what Compose/Prometheus/
Grafana actually read, so picking features is one file edit + one command,
not hand-editing docker-compose.yml, prometheus.yml, and the dashboard
folder separately every time.

    routeros_exporter/.venv/bin/python scripts/apply_features.py
    docker compose up -d

What it does, every run (idempotent - safe to re-run any time):

  1. Reads features.yml (falls back to features.example.yml with a warning,
     so a first-time `docker compose up` doesn't silently mount nothing).
  2. Resolves the enabled feature set against feature-manifest.yml -
     base_monitoring is always included.
  3. Rebuilds prometheus/rules.active/ and grafana/dashboards.active/ as
     plain copies of only the enabled features' files - these, not
     prometheus/rules/ or grafana/dashboards/, are what docker-compose.yml
     actually bind-mounts (see its comments). Copies, not symlinks: only
     the *.active directory itself is bind-mounted into each container, so
     a symlink pointing back at .../prometheus/rules/foo.yml (a host path
     outside that mount) would dangle inside the container - it has no
     view of the rest of the host filesystem. This means a rule/dashboard
     file edit needs a re-run of this script to take effect, same as a
     features.yml change does.
  4. Writes .env with COMPOSE_PROFILES=<union of enabled features'
     compose_profiles> - Compose reads COMPOSE_PROFILES from .env
     automatically, so a plain `docker compose up -d` (no --profile flags)
     starts exactly the services the enabled features need.

Nothing here touches routeros_exporter/config.yml's per-router
collect_ppp/collect_backup/collect_topology flags - those stay the
operator's day-to-day dial once a feature is switched on here; see
routeros_exporter/README.md.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "feature-manifest.yml"
FEATURES_PATH = ROOT / "features.yml"
FEATURES_EXAMPLE_PATH = ROOT / "features.example.yml"
RULES_SRC = ROOT / "prometheus" / "rules"
RULES_ACTIVE = ROOT / "prometheus" / "rules.active"
DASHBOARDS_SRC = ROOT / "grafana" / "dashboards"
DASHBOARDS_ACTIVE = ROOT / "grafana" / "dashboards.active"
ENV_PATH = ROOT / ".env"


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))["features"]


def load_choices() -> dict:
    path = FEATURES_PATH
    if not path.exists():
        print(
            f"WARNING: {FEATURES_PATH.name} not found - falling back to "
            f"{FEATURES_EXAMPLE_PATH.name}'s defaults. Copy it to "
            f"{FEATURES_PATH.name} to make your own choices stick "
            f"(it's gitignored, same as routeros_exporter/config.yml).",
            file=sys.stderr,
        )
        path = FEATURES_EXAMPLE_PATH
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def resolve_enabled(manifest: dict, choices: dict) -> list[str]:
    enabled = []
    for name, spec in manifest.items():
        if spec.get("always_on") or choices.get(name, False):
            enabled.append(name)
    unknown = set(choices) - set(manifest)
    if unknown:
        print(
            f"WARNING: features.yml names unknown feature(s) {sorted(unknown)} "
            f"not present in feature-manifest.yml - ignored.",
            file=sys.stderr,
        )
    return enabled


def rebuild_active_dir(active_dir: pathlib.Path, src_dir: pathlib.Path, filenames: list[str]) -> None:
    """Rebuild `active_dir` from scratch as flat copies of `filenames` from
    `src_dir` - safe to blow away and recreate every run since it never
    holds anything but generated output. Copies rather than symlinks - see
    module docstring for why a symlink back to the source directory would
    dangle once only `active_dir` is bind-mounted into a container."""
    if active_dir.exists():
        for child in active_dir.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
            # (no subdirectories expected; nothing else to clean up)
    active_dir.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        src = src_dir / name
        if not src.exists():
            raise FileNotFoundError(
                f"feature-manifest.yml references {src.relative_to(ROOT)} "
                f"which does not exist - fix the manifest or the file"
            )
        shutil.copy2(src, active_dir / name)


def main() -> int:
    manifest = load_manifest()
    choices = load_choices()
    enabled = resolve_enabled(manifest, choices)

    rule_files: list[str] = []
    dashboards: list[str] = []
    profiles: set[str] = set()
    for name in enabled:
        spec = manifest[name]
        rule_files.extend(spec.get("rule_files", []))
        dashboards.extend(spec.get("dashboards", []))
        profiles.update(spec.get("compose_profiles", []))

    rebuild_active_dir(RULES_ACTIVE, RULES_SRC, sorted(set(rule_files)))
    rebuild_active_dir(DASHBOARDS_ACTIVE, DASHBOARDS_SRC, sorted(set(dashboards)))

    env_lines = []
    if ENV_PATH.exists():
        env_lines = [
            line for line in ENV_PATH.read_text(encoding="utf-8").splitlines()
            if not line.startswith("COMPOSE_PROFILES=")
        ]
    env_lines.append(f"COMPOSE_PROFILES={','.join(sorted(profiles))}")
    ENV_PATH.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    disabled = sorted(set(manifest) - set(enabled))
    print(f"Enabled features:  {', '.join(sorted(enabled))}")
    print(f"Disabled features: {', '.join(disabled) or '(none)'}")
    print(f"Compose profiles:  {','.join(sorted(profiles)) or '(none - base services only)'}")
    print(f"{len(rule_files)} rule file(s) copied into {RULES_ACTIVE.relative_to(ROOT)}/")
    print(f"{len(dashboards)} dashboard(s) copied into {DASHBOARDS_ACTIVE.relative_to(ROOT)}/")
    print("Now run: docker compose up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
