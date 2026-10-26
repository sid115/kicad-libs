#!/usr/bin/env python3
"""Build build/parts.sqlite from parts.yaml."""

from __future__ import annotations

import argparse
import math
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 2

DB_FIELDS = [
    "key",
    "part_id",
    "symbol",
    "value",
    "description",
    "keywords",
    "datasheet",
    "footprint",
    "package",
    "height_mm",
    "mpn",
    "manufacturer",
]


class PartsError(Exception):
    """Raised when parts.yaml or the referenced libraries are invalid."""


def load_parts(path: Path) -> dict:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PartsError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise PartsError(f"{path}: top level must be a mapping")
    if doc.get("version") != 1:
        raise PartsError(f"{path}: unsupported version {doc.get('version')!r}")
    if not isinstance(doc.get("parts"), list) or not doc["parts"]:
        raise PartsError(f"{path}: 'parts' must be a non-empty list")
    return doc


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _overlay(
    part_id: str, field: str, yaml_value: str | None, symbol_value: str | None
) -> str:
    if yaml_value:
        if symbol_value and yaml_value != symbol_value:
            _warn(
                f"part {part_id!r}: {field} differs from symbol; "
                "using parts.yaml value"
            )
        return yaml_value
    return symbol_value or ""


def _symbol_properties(symbol: str, root: Path | None) -> dict[str, str]:
    if root is None:
        return {}
    nickname, name = _split_lib_id(symbol, "symbol")
    properties = read_symbol_properties(resolve_symbol(symbol, root), name)
    if properties is None:
        raise PartsError(f"symbol {symbol!r} not found under symbols/")
    return properties


def flatten_parts(doc: dict, root: Path | None = None) -> list[dict]:
    rows = []
    for part in doc["parts"]:
        part_id = part["id"]
        symbol = part["symbol"]
        properties = _symbol_properties(symbol, root)

        value = _overlay(part_id, "value", part.get("value"), properties.get("Value"))
        description = _overlay(
            part_id,
            "description",
            part.get("description"),
            properties.get("Description"),
        )
        datasheet = _overlay(
            part_id,
            "datasheet",
            part.get("datasheet"),
            properties.get("Datasheet"),
        )
        keywords = _overlay(
            part_id,
            "keywords",
            part.get("keywords"),
            properties.get("Keywords"),
        )

        footprints = part.get("footprints", [])
        symbol_footprint = properties.get("Footprint", "")
        if len(footprints) == 1:
            footprint_id = footprints[0].get("footprint")
            if (
                footprint_id
                and symbol_footprint
                and footprint_id != symbol_footprint
            ):
                _warn(
                    f"part {part_id!r}: footprint differs from symbol; "
                    "using parts.yaml value"
                )

        for footprint in footprints:
            for variant in footprint.get("mpns", []):
                rows.append(
                    {
                        "key": variant["mpn"],
                        "part_id": part_id,
                        "symbol": symbol,
                        "value": value,
                        "description": variant.get("description") or description,
                        "keywords": keywords,
                        "datasheet": variant.get("datasheet") or datasheet,
                        "footprint": footprint.get("footprint") or symbol_footprint,
                        "package": footprint.get("package", ""),
                        "height_mm": variant.get("height_mm"),
                        "mpn": variant["mpn"],
                        "manufacturer": variant.get("manufacturer", ""),
                    }
                )
    return rows


def _split_lib_id(lib_id: str, kind: str) -> tuple[str, str]:
    if not isinstance(lib_id, str) or ":" not in lib_id:
        raise PartsError(f"{kind} LIB_ID must be '<nickname>:<name>', got {lib_id!r}")
    nickname, name = lib_id.split(":", 1)
    if not nickname or not name:
        raise PartsError(f"{kind} LIB_ID must be '<nickname>:<name>', got {lib_id!r}")
    return nickname, name


def _tokenize_sexpr(text: str) -> list[tuple[str, str | None]]:
    tokens: list[tuple[str, str | None]] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif char == "(":
            tokens.append(("open", None))
            index += 1
        elif char == ")":
            tokens.append(("close", None))
            index += 1
        elif char == '"':
            index += 1
            value = []
            while index < length:
                char = text[index]
                if char == "\\" and index + 1 < length:
                    escaped = text[index + 1]
                    value.append(
                        {"n": "\n", "t": "\t", "r": "\r"}.get(escaped, escaped)
                    )
                    index += 2
                elif char == '"':
                    index += 1
                    break
                else:
                    value.append(char)
                    index += 1
            tokens.append(("string", "".join(value)))
        else:
            start = index
            while index < length and text[index] not in ' \t\r\n()"':
                index += 1
            tokens.append(("atom", text[start:index]))

    return tokens


def _parse_sexpr(text: str):
    tokens = _tokenize_sexpr(text)
    position = 0

    def parse():
        nonlocal position
        kind, value = tokens[position]
        position += 1
        if kind == "open":
            items = []
            while tokens[position][0] != "close":
                items.append(parse())
            position += 1
            return items
        return (kind, value)

    return parse()


def _node_text(node) -> str | None:
    if isinstance(node, tuple) and node[0] in ("atom", "string"):
        return node[1]
    return None


def read_symbol_properties(path: Path, name: str) -> dict[str, str] | None:
    tree = _parse_sexpr(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(tree, list):
        return None

    for node in tree:
        if not isinstance(node, list) or len(node) < 2:
            continue
        if _node_text(node[0]) != "symbol" or _node_text(node[1]) != name:
            continue

        properties: dict[str, str] = {}
        for item in node[2:]:
            if not isinstance(item, list) or len(item) < 3:
                continue
            if _node_text(item[0]) != "property":
                continue
            key = _node_text(item[1])
            value = _node_text(item[2])
            if key is not None and value is not None:
                properties[key] = value
        return properties

    return None


def _top_level_symbol_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith('\t(symbol "') and not line.startswith('\t\t(symbol "'):
            names.append(line.split('"')[1])
    return names


def resolve_symbol(lib_id: str, root: Path) -> Path:
    nickname, name = _split_lib_id(lib_id, "symbol")
    for path in sorted(root.joinpath("symbols").rglob("*.kicad_sym")):
        if path.stem == nickname and name in _top_level_symbol_names(path):
            return path
    raise PartsError(f"symbol {lib_id!r} not found under symbols/")


def resolve_footprint(lib_id: str, root: Path) -> Path:
    nickname, name = _split_lib_id(lib_id, "footprint")
    path = root / "footprints" / f"{nickname}.pretty" / f"{name}.kicad_mod"
    if not path.is_file():
        raise PartsError(
            f"footprint {lib_id!r} not found at {path.relative_to(root)}"
        )
    return path


def _model_paths(footprint_path: Path) -> list[str]:
    text = footprint_path.read_text(encoding="utf-8", errors="replace")
    return re.findall(r'\(model\s+"([^"]+)"', text)


def check_models(footprint_path: Path, root: Path) -> None:
    for raw in _model_paths(footprint_path):
        expanded = raw.replace("${KICAD_LIBS}", str(root))
        if "${" in expanded:
            raise PartsError(
                f"{footprint_path.name}: unsupported variable in model path {raw!r}"
            )
        model = Path(expanded)
        if not model.is_absolute():
            model = root / model
        if not model.is_file():
            raise PartsError(
                f"{footprint_path.name}: 3D model not found: {raw!r}"
            )


def validate_parts(doc: dict, root: Path) -> None:
    part_ids: set[str] = set()
    mpns: set[str] = set()

    for part in doc["parts"]:
        part_id = part.get("id")
        if not part_id:
            raise PartsError("part is missing required field 'id'")
        if part_id in part_ids:
            raise PartsError(f"duplicate part id {part_id!r}")
        part_ids.add(part_id)

        if not part.get("symbol"):
            raise PartsError(f"part {part_id!r}: missing required field 'symbol'")
        properties = _symbol_properties(part["symbol"], root)

        footprints = part.get("footprints")
        if not footprints:
            raise PartsError(f"part {part_id!r}: no footprints")

        for footprint in footprints:
            footprint_id = footprint.get("footprint") or properties.get("Footprint")
            if not footprint_id:
                raise PartsError(
                    f"part {part_id!r}: footprint entry is missing 'footprint' "
                    "and symbol has no Footprint"
                )
            footprint_path = resolve_footprint(footprint_id, root)
            check_models(footprint_path, root)

            variants = footprint.get("mpns")
            if not variants:
                raise PartsError(
                    f"part {part_id!r}: footprint {footprint_id!r} has no MPNs"
                )
            for variant in variants:
                mpn = variant.get("mpn")
                if not mpn:
                    raise PartsError(
                        f"part {part_id!r}: MPN entry is missing 'mpn'"
                    )
                if mpn in mpns:
                    raise PartsError(f"duplicate MPN {mpn!r}")
                mpns.add(mpn)

                height_mm = variant.get("height_mm")
                if height_mm is not None:
                    if (
                        isinstance(height_mm, bool)
                        or not isinstance(height_mm, (int, float))
                        or not math.isfinite(height_mm)
                        or height_mm <= 0
                    ):
                        raise PartsError(
                            f"part {part_id!r}: MPN {mpn!r} height_mm must be "
                            "a positive number"
                        )


PARTS_TABLE_SQL = """
CREATE TABLE parts (
    key TEXT PRIMARY KEY,
    part_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    value TEXT NOT NULL,
    description TEXT NOT NULL,
    keywords TEXT NOT NULL,
    datasheet TEXT NOT NULL,
    footprint TEXT NOT NULL,
    package TEXT NOT NULL,
    height_mm REAL,
    mpn TEXT NOT NULL,
    manufacturer TEXT NOT NULL
)
"""

META_TABLE_SQL = """
CREATE TABLE _meta (
    schema_version INTEGER NOT NULL,
    generator TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source_commit TEXT NOT NULL
)
"""


def write_sqlite(rows: list[dict], output: Path, source_commit: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temp = Path(temp_name)

    try:
        connection = sqlite3.connect(temp)
        try:
            connection.execute(PARTS_TABLE_SQL)
            connection.execute(META_TABLE_SQL)
            placeholders = ", ".join(f":{field}" for field in DB_FIELDS)
            connection.executemany(
                f"INSERT INTO parts ({', '.join(DB_FIELDS)}) VALUES ({placeholders})",
                rows,
            )
            connection.execute(
                "INSERT INTO _meta VALUES (?, ?, ?, ?)",
                (
                    SCHEMA_VERSION,
                    "build_parts_db.py",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    source_commit,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(temp, output)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def build(parts_path: Path, output: Path, root: Path, source_commit: str) -> list[dict]:
    doc = load_parts(parts_path)
    validate_parts(doc, root)
    rows = flatten_parts(doc, root)
    write_sqlite(rows, output, source_commit)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", type=Path, default=ROOT / "parts.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "parts.sqlite")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-commit", default="unknown")
    args = parser.parse_args(argv)

    try:
        rows = build(args.parts, args.output, args.root, args.source_commit)
    except PartsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"built {args.output} ({len(rows)} MPN rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
