"""Static pnpm lock preflight; does not install or qualify package behavior."""
import argparse
import json
from pathlib import Path
import sys
import yaml

parser = argparse.ArgumentParser()
parser.add_argument("batch", choices=["root", "ui", "graph"])
args = parser.parse_args()
lock = yaml.safe_load(Path("pnpm-lock.yaml").read_text())
errors = []
if args.batch in {"root", "ui"}:
    prefix = "" if args.batch == "root" else "ui/"
    importer = "." if args.batch == "root" else "ui"
    manifest = json.loads(Path(prefix + "package.json").read_text())
    recorded = lock["importers"].get(importer, {})
    for kind in ("dependencies", "devDependencies", "optionalDependencies"):
        declared = manifest.get(kind, {})
        entries = recorded.get(kind, {})
        for name, spec in declared.items():
            if entries.get(name, {}).get("specifier") != spec:
                errors.append(f"{importer}: {name} manifest/lock specifier mismatch")
        for name in set(entries) - set(declared):
            errors.append(f"{importer}: undeclared {kind} entry {name}")
    if importer == "ui":
        versions = manifest.get("devDependencies", {})
        if versions.get("vitest") != versions.get("@vitest/browser-playwright"):
            errors.append("ui: Vitest and browser adapter versions require coordinated review")
else:
    snapshots = lock["snapshots"]
    for name, snapshot in snapshots.items():
        package = name.split("(", 1)[0]
        if package not in lock["packages"]:
            errors.append(f"snapshot has no package resolution: {name}")
        for kind in ("dependencies", "optionalDependencies"):
            for dep, version in snapshot.get(kind, {}).items():
                if version.startswith(("link:", "file:")):
                    continue
                if dep + "@" + version not in snapshots and version not in snapshots:
                    errors.append(f"missing snapshot: {name} -> {dep}@{version}")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"PASS: {args.batch} static preflight")
print("No install, browser/native runtime compatibility, or security clearance established.")
