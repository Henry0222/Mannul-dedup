"""Collect licenses of the JavaScript packages embedded in the VOS viewer."""

from pathlib import Path


root = Path(__file__).resolve().parent
packages = root / "node_modules"
output = root / "dist" / "THIRD_PARTY_NOTICES.txt"
parts = ["JavaScript dependencies used by Mannul-dedup VOS viewer\n"]
for path in sorted(packages.rglob("*")):
    if not path.is_file() or not path.name.lower().startswith(("license", "licence", "copying")):
        continue
    if path.stat().st_size > 200_000:
        continue
    content = path.read_text(encoding="utf-8", errors="replace")
    parts.append(f"\n{'=' * 72}\n{path.relative_to(packages)}\n{'=' * 72}\n{content}\n")
output.write_text("".join(parts), encoding="utf-8")
print(f"Collected {len(parts) - 1} license files into {output}")
