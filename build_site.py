#!/usr/bin/env python3
"""Build the static parts catalogue from parts.yaml."""

import argparse
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from build_parts_db import PartsError, flatten_parts, load_parts, validate_parts

DATA_FIELDS = (
    "part_id",
    "value",
    "description",
    "mpn",
    "manufacturer",
    "package",
    "datasheet",
)

STRING_FIELDS = (
    "part_id",
    "value",
    "description",
    "mpn",
    "manufacturer",
    "package",
)

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"
ASSET_FILES = ("index.html", "app.js", "style.css")


def sanitise_datasheet(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return value


def site_rows(doc: dict, root: Path | None = None) -> list[dict]:
    rows = []
    for row in flatten_parts(doc, root):
        projected = {field: row.get(field) for field in DATA_FIELDS}
        for field in STRING_FIELDS:
            value = projected[field]
            if not isinstance(value, str):
                raise PartsError(
                    f"part {row.get('part_id')!r}: field {field!r} "
                    f"must be a string, got {value!r}"
                )
        projected["datasheet"] = sanitise_datasheet(row.get("datasheet"))
        rows.append(projected)
    return rows


def write_site(
    rows: list[dict],
    output: Path,
    site_dir: Path = SITE_DIR,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name in ASSET_FILES:
        shutil.copyfile(site_dir / name, output / name)
    (output / "parts.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build(
    parts_path: Path,
    output: Path,
    root: Path = ROOT,
    site_dir: Path = SITE_DIR,
) -> list[dict]:
    doc = load_parts(parts_path)
    validate_parts(doc, root)
    rows = site_rows(doc, root)
    write_site(rows, output, site_dir)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", type=Path, default=ROOT / "parts.yaml")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("build") / "site")
    args = parser.parse_args(argv)

    try:
        rows = build(args.parts, args.output, args.root)
    except PartsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"built {args.output / 'index.html'} ({len(rows)} MPN rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
