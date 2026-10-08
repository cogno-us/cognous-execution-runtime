"""Static pnpm lock checks; no package installation or runtime qualification."""
import argparse
import json
from pathlib import Path
import re
import sys
import yaml

KINDS = ("dependencies", "devDependencies", "optionalDependencies")


def snapshot_key(name, version, snapshots):
    if version.startswith(("link:", "file:")):
        return None
    # pnpm aliases retain the actual package name in the resolution value.
    return name + "@" + version if name + "@" + version in snapshots else version


def check_manifest(manifest, lock, importer):
    errors = []
    recorded = lock["importers"].get(importer, {})
    for kind in KINDS:
        declared, entries = manifest.get(kind, {}), recorded.get(kind, {})
        for name, spec in declared.items():
            entry = entries.get(name, {})
            if entry.get("specifier") != spec:
                errors.append(f"{importer}: {name} manifest/lock specifier mismatch")
            version = entry.get("version")
            if not isinstance(version, str):
                errors.append(f"{importer}: {name} missing locked version")
                continue
            key = snapshot_key(name, version, lock["snapshots"])
            if key is not None and key not in lock["snapshots"]:
                errors.append(f"{importer}: {name} missing importer snapshot")
            if re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", spec):
                if version.split("(", 1)[0] != spec:
                    errors.append(f"{importer}: {name} exact version/resolution mismatch")
        for name in set(entries) - set(declared):
            errors.append(f"{importer}: undeclared {kind} entry {name}")
    if importer == "ui":
        versions = manifest.get("devDependencies", {})
        if versions.get("vitest") != versions.get("@vitest/browser-playwright"):
            errors.append("ui: Vitest and browser adapter versions require coordinated review")
    return errors


def check_graph(lock):
    errors = []
    snapshots = lock["snapshots"]
    for name, snapshot in snapshots.items():
        if name.split("(", 1)[0] not in lock["packages"]:
            errors.append(f"snapshot has no package resolution: {name}")
        for kind in ("dependencies", "optionalDependencies"):
            for dep, version in snapshot.get(kind, {}).items():
                key = snapshot_key(dep, version, snapshots)
                if key is not None and key not in snapshots:
                    errors.append(f"missing snapshot: {name} -> {dep}@{version}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=["root", "ui", "graph"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        lock = yaml.safe_load((args.root / "pnpm-lock.yaml").read_text())
        if args.batch == "graph":
            errors = check_graph(lock)
        else:
            prefix = Path() if args.batch == "root" else Path("ui")
            manifest = json.loads((args.root / prefix / "package.json").read_text())
            errors = check_manifest(manifest, lock, "." if args.batch == "root" else "ui")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
        print(f"Invalid preflight input: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors))
        return 1
    print(f"PASS: {args.batch} static preflight")
    print("No install, semver-range solving, runtime compatibility, or security clearance established.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
