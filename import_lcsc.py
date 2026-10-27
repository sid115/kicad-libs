#!/usr/bin/env python3
"""Import one part by MPN from the JLCPCB/LCSC ecosystem."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml


JLCPCB_SEARCH_URL = (
    "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/"
    "smtGood/selectSmtComponentList"
)

MAX_SEARCH_RESULTS = 100

JLC_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://jlcpcb.com",
    "Referer": "https://jlcpcb.com/parts",
    "User-Agent": "kicad-libs-import-lcsc/1.0",
}


class LcscImportError(Exception):
    """A user-facing import failure."""


def _as_text(value: object) -> str:
    return "" if value is None else str(value)


def _as_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _library_type(value: object) -> str:
    if value == "base":
        return "Basic"
    if value == "expand":
        return "Extended"
    return ""


def normalize_mpn(value: object) -> str:
    return " ".join(_as_text(value).split()).casefold()


def dedupe_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for candidate in candidates:
        code = _as_text(candidate.get("lcsc", ""))
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(candidate)
    return result


def search_jlcpcb(
    mpn: str, *, page_size: int = MAX_SEARCH_RESULTS, timeout: float = 15.0
) -> list[dict]:
    payload = {"keyword": mpn, "currentPage": 1, "pageSize": page_size}
    request = urllib.request.Request(
        JLCPCB_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=JLC_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LcscImportError(
            f"JLCPCB lookup failed for {mpn!r}: {exc}"
        ) from exc

    page_info = (raw.get("data") or {}).get("componentPageInfo") or {}
    items = page_info.get("list") or []
    results: list[dict] = []
    for item in items:
        results.append(
            {
                "lcsc": _as_text(item.get("componentCode")),
                "mpn": _as_text(item.get("componentModelEn")),
                "manufacturer": _as_text(item.get("componentBrandEn")),
                "package": _as_text(item.get("componentSpecificationEn")),
                "category": _as_text(item.get("componentTypeEn")),
                "stock": _as_int(item.get("stockCount")),
                "type": _library_type(item.get("componentLibraryType")),
                "description": _as_text(item.get("describe")),
                "name": _as_text(item.get("componentName")),
                "datasheet": _as_text(item.get("dataManualUrl")),
                "url": _as_text(item.get("lcscGoodsUrl")),
            }
        )
    return dedupe_candidates(results)


def select_exact_mpn(mpn: str, candidates: list[dict]) -> dict:
    wanted = normalize_mpn(mpn)
    exact = [c for c in candidates if normalize_mpn(c.get("mpn", "")) == wanted]
    if len(exact) == 1:
        return exact[0]
    if not exact:
        raise LcscImportError(f"no exact MPN match for {mpn!r}")
    raise LcscImportError(f"multiple exact MPN matches for {mpn!r}")


def format_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return "No candidates found."
    lines = [
        f"{'C#':<8} {'MPN':<24} {'Manufacturer':<20} "
        f"{'Package':<20} {'Stock':>8} Type"
    ]
    for candidate in candidates[:MAX_SEARCH_RESULTS]:
        lines.append(
            f"{one_line(candidate.get('lcsc')):<8} "
            f"{one_line(candidate.get('mpn')):<24} "
            f"{one_line(candidate.get('manufacturer')):<20} "
            f"{one_line(candidate.get('package')):<20} "
            f"{_as_int(candidate.get('stock')):>8} "
            f"{one_line(candidate.get('type'))}"
        )
    return "\n".join(lines)


def _unescape_sexpr(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _escape_sexpr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def top_level_symbol_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    depth = 0
    index = 0
    in_string = False
    escaped = False
    start: int | None = None
    name: str | None = None

    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char == "(":
            if depth == 1:
                match = re.match(
                    r'\(symbol\s+"((?:[^"\\]|\\.)*)"', text[index:]
                )
                if match:
                    start = index
                    name = _unescape_sexpr(match.group(1))
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 1 and start is not None and name is not None:
                blocks[name] = text[start : index + 1]
                start = None
                name = None
        index += 1
    return blocks


def extract_single_symbol_from_text(text: str) -> tuple[str, str]:
    blocks = top_level_symbol_blocks(text)
    if len(blocks) != 1:
        raise LcscImportError(
            f"expected exactly one top-level symbol, found {sorted(blocks)}"
        )
    name, block = next(iter(blocks.items()))
    return name, block


def extract_single_symbol(path: Path) -> tuple[str, str]:
    return extract_single_symbol_from_text(path.read_text(encoding="utf-8"))


def symbol_properties_from_block(block: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    pattern = re.compile(
        r'\(property\s+"([^"]+)"\s+"((?:[^"\\]|\\.)*)"'
    )
    for match in pattern.finditer(block):
        properties[match.group(1)] = _unescape_sexpr(match.group(2))
    return properties


def rename_symbol(block: str, old: str, new: str) -> str:
    if old == new:
        return block
    pattern = re.compile(
        r'(\(symbol\s+)"' + re.escape(old) + r'(_[0-9_]+)?"'
    )

    def replace(match: re.Match) -> str:
        return match.group(1) + '"' + new + (match.group(2) or "") + '"'

    renamed = pattern.sub(replace, block)
    if symbol_properties_from_block(renamed).get("Value") == old:
        renamed = set_symbol_property(renamed, "Value", new)
    return renamed


def set_symbol_property(block: str, name: str, value: str) -> str:
    escaped = _escape_sexpr(value)
    pattern = re.compile(
        r'(\(property\s+"' + re.escape(name) + r'"\s+)"(?:[^"\\]|\\.)*"'
    )

    def replace(match: re.Match) -> str:
        return match.group(1) + '"' + escaped + '"'

    if pattern.search(block):
        return pattern.sub(replace, block, count=1)

    prop = (
        f'(property "{name}" "{escaped}" (at 0 0 0) '
        f'(effects (font (size 1.27 1.27)) hide))'
    )
    nested = re.search(r'\n[ \t]*\(symbol\s+"', block)
    if nested:
        return block[: nested.start()] + "\n    " + prop + block[nested.start() :]
    return block[:-1] + "\n    " + prop + "\n)"


def add_symbol_metadata(block: str, props: dict[str, str]) -> str:
    block = block.replace('"ki_keywords"', '"Keywords"', 1)
    for name in ("Value", "Description", "Datasheet", "Keywords", "Footprint"):
        value = props.get(name, "")
        if value:
            block = set_symbol_property(block, name, value)
    return block


def _indent_block(block: str) -> str:
    return "\n".join(
        "\t" + line if line.strip() else line for line in block.splitlines()
    )


def _indent_with(block: str, indent: str) -> str:
    return "\n".join(
        indent + line if line.strip() else line for line in block.splitlines()
    )


def _dedent_block(block: str) -> str:
    lines = block.splitlines()
    if len(lines) < 2:
        return block
    indents = [
        re.match(r"[ \t]*", line).group(0)
        for line in lines[1:]
        if line.strip()
    ]
    if not indents:
        return block
    common = indents[0]
    for indent in indents[1:]:
        index = 0
        while (
            index < len(common)
            and index < len(indent)
            and common[index] == indent[index]
        ):
            index += 1
        common = common[:index]
    if not common:
        return block
    return "\n".join(
        [lines[0]]
        + [
            line[len(common) :] if line.strip() else line
            for line in lines[1:]
        ]
    )


def merge_symbol_library(
    target_text: str | None,
    name: str,
    block: str,
    *,
    replace: bool = False,
) -> str:
    if not target_text:
        header = (
            "(kicad_symbol_lib\n"
            '\t(version 20251024)\n'
            '\t(generator "import_lcsc")\n'
            '\t(generator_version "10.0")\n'
        )
        return header + _indent_block(block) + "\n)\n"

    existing = top_level_symbol_blocks(target_text)
    if name in existing:
        if not replace:
            raise LcscImportError(
                f"symbol {name!r} already exists in target library"
            )
        old_block = existing[name]
        start = target_text.find(old_block)
        if start < 0:
            raise LcscImportError(
                f"symbol {name!r} block not found in target library"
            )
        line_start = target_text.rfind("\n", 0, start) + 1
        indentation = target_text[line_start:start]
        if indentation.strip():
            indentation = ""
            replace_start = start
        else:
            replace_start = line_start
        replacement = _indent_with(_dedent_block(block), indentation)
        return (
            target_text[:replace_start]
            + replacement
            + target_text[start + len(old_block) :]
        )

    closing = target_text.rstrip().rfind(")")
    if closing < 0:
        raise LcscImportError("target symbol library has no closing parenthesis")
    body = target_text[:closing].rstrip("\n")
    return body + "\n" + _indent_block(block) + "\n)\n"


def validate_name(value: str, kind: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or ":" in value
    ):
        raise LcscImportError(f"invalid {kind} name {value!r}")
    return value


def generated_outputs(stem: Path) -> dict[str, Path]:
    return {
        "symbol": Path(str(stem) + ".kicad_sym"),
        "pretty": Path(str(stem) + ".pretty"),
        "shapes": Path(str(stem) + ".3dshapes"),
    }


def run_easyeda2kicad(
    lcsc_id: str, stem: Path, executable: str = "easyeda2kicad"
) -> None:
    command = [
        executable,
        "--lcsc_id",
        lcsc_id,
        "--full",
        "--overwrite",
        "--output",
        str(stem),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise LcscImportError(
            f"easyeda2kicad failed for {lcsc_id}: {detail}"
        )


def read_single_footprint(pretty_dir: Path) -> Path:
    files = sorted(pretty_dir.glob("*.kicad_mod"))
    if len(files) != 1:
        raise LcscImportError(
            f"expected exactly one generated footprint in {pretty_dir}, "
            f"found {len(files)}"
        )
    return files[0]


def read_single_step(shapes_dir: Path) -> Path:
    files = sorted(
        list(shapes_dir.glob("*.step")) + list(shapes_dir.glob("*.stp"))
    )
    if len(files) != 1:
        raise LcscImportError(
            f"expected exactly one generated STEP model in {shapes_dir}, "
            f"found {len(files)}"
        )
    return files[0]


def transform_footprint(
    text: str, old_name: str, new_name: str, model_name: str
) -> str:
    text = re.sub(
        r'\(footprint\s+"' + re.escape(old_name) + r'"',
        f'(footprint "{new_name}"',
        text,
        count=1,
    )
    replacement = '"${KICAD_LIBS}/3dmodels/' + model_name + '.step"'
    text, count = re.subn(
        r'(\(model\s+)"(?:[^"\\]|\\.)*"',
        lambda match: match.group(1) + replacement,
        text,
    )
    if count == 0:
        raise LcscImportError(
            f"generated footprint {old_name!r} has no model block"
        )
    return text


def one_line(value: object) -> str:
    return " ".join(_as_text(value).split())


def build_keywords(*values: str) -> str:
    words: list[str] = []
    for value in values:
        if not value:
            continue
        for word in str(value).split():
            if word not in words:
                words.append(word)
    return " ".join(words)


def build_part_entry(
    *,
    mpn: str,
    lcsc: dict,
    symbol_lib: str,
    symbol_name: str,
    footprint_lib: str,
    footprint_name: str,
    package: str,
    symbol_props: dict[str, str],
    part_id: str | None = None,
    height_mm: float | None = None,
) -> dict:
    description = one_line(
        lcsc.get("description")
        or lcsc.get("name")
        or symbol_props.get("Description", "")
        or mpn
    )
    datasheet = (
        lcsc.get("datasheet")
        or symbol_props.get("Datasheet", "")
    )
    keywords = build_keywords(
        symbol_props.get("Keywords", ""),
        lcsc.get("category", ""),
        mpn,
        lcsc.get("manufacturer", ""),
    )
    variant = {"mpn": mpn}
    manufacturer = one_line(lcsc.get("manufacturer", ""))
    if manufacturer:
        variant["manufacturer"] = manufacturer
    if height_mm is not None:
        variant["height_mm"] = height_mm

    return {
        "id": part_id or mpn,
        "symbol": f"{symbol_lib}:{symbol_name}",
        "value": mpn,
        "description": description,
        "datasheet": datasheet,
        "keywords": keywords,
        "footprints": [
            {
                "footprint": f"{footprint_lib}:{footprint_name}",
                "package": package or footprint_name,
                "mpns": [variant],
            }
        ],
    }


def append_part_yaml_text(text: str, entry: dict) -> str:
    rendered = yaml.safe_dump(
        [entry],
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip("\n")
    block = "\n".join("  " + line for line in rendered.splitlines())
    separator = "" if text.endswith("\n") else "\n"
    return text + separator + block + "\n"


def append_part_yaml(path: Path, entry: dict) -> None:
    path.write_text(
        append_part_yaml_text(path.read_text(encoding="utf-8"), entry),
        encoding="utf-8",
    )


def yaml_collisions(doc: dict, part_id: str, mpn: str) -> list[str]:
    errors: list[str] = []
    for part in doc.get("parts", []):
        if part.get("id") == part_id:
            errors.append(f"part id {part_id!r} already exists")
        for footprint in part.get("footprints", []):
            for variant in footprint.get("mpns", []):
                if variant.get("mpn") == mpn:
                    errors.append(f"MPN {mpn!r} already exists")
    return errors


LIB_ROW_PATTERN = re.compile(
    r'\(lib\s+\(name\s+"([^"]+)"\).*?\(uri\s+"([^"]+)"\)',
    re.DOTALL,
)


def library_rows(table_text: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2)
        for match in LIB_ROW_PATTERN.finditer(table_text)
    }


def library_uri(kind: str, nick: str) -> str:
    if kind == "symbol":
        return f"${{KICAD_LIBS}}/symbols/{nick}.kicad_sym"
    if kind == "footprint":
        return f"${{KICAD_LIBS}}/footprints/{nick}.pretty"
    raise LcscImportError(f"unknown library kind {kind!r}")


def check_library_registration(
    table_text: str, nick: str, uri: str
) -> None:
    existing = library_rows(table_text)
    if nick in existing and existing[nick] != uri:
        raise LcscImportError(
            f"library {nick!r} is registered with a different URI: "
            f"{existing[nick]!r}"
        )


def append_library_row(
    table_text: str, nick: str, uri: str, descr: str
) -> str:
    existing = library_rows(table_text)
    if nick in existing:
        if existing[nick] != uri:
            raise LcscImportError(
                f"library {nick!r} is registered with a different URI: "
                f"{existing[nick]!r}"
            )
        return table_text

    closing = table_text.rstrip().rfind(")")
    if closing < 0:
        raise LcscImportError("library table has no closing parenthesis")
    row = (
        f'\t(lib (name "{nick}") (type "KiCad") (uri "{uri}") '
        f'(options "") (descr "{_escape_sexpr(descr)}"))'
    )
    body = table_text[:closing].rstrip("\n")
    return body + "\n" + row + "\n)\n"


def check_collisions(
    *,
    root: Path,
    symbol_lib: str,
    symbol_name: str,
    footprint_lib: str,
    footprint_name: str,
    model_name: str,
    doc: dict,
    part_id: str,
    mpn: str,
    replace: bool,
) -> None:
    errors: list[str] = []
    symbol_path = root / "symbols" / f"{symbol_lib}.kicad_sym"
    if (
        symbol_path.exists()
        and symbol_name in top_level_symbol_blocks(
            symbol_path.read_text(encoding="utf-8")
        )
        and not replace
    ):
        errors.append(f"symbol {symbol_name!r} already exists")

    footprint_path = (
        root / "footprints" / f"{footprint_lib}.pretty" / f"{footprint_name}.kicad_mod"
    )
    if footprint_path.exists() and not replace:
        errors.append(f"footprint {footprint_name!r} already exists")

    model_path = root / "3dmodels" / f"{model_name}.step"
    if model_path.exists() and not replace:
        errors.append(f"3D model {model_name!r} already exists")

    errors.extend(yaml_collisions(doc, part_id, mpn))
    if errors:
        raise LcscImportError("\n".join(errors))


@dataclass
class PlannedChange:
    path: Path
    content: bytes | None


def apply_changes(changes: list[PlannedChange]) -> None:
    originals: dict[Path, bytes | None] = {}
    temporaries: list[Path] = []
    try:
        for change in changes:
            if change.path not in originals:
                originals[change.path] = (
                    change.path.read_bytes() if change.path.exists() else None
                )
            if change.content is None:
                change.path.unlink(missing_ok=True)
                continue
            change.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = change.path.with_name(change.path.name + ".tmp")
            temporaries.append(temporary)
            temporary.write_bytes(change.content)
            os.replace(temporary, change.path)
    except BaseException:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        raise


def validate_in_shadow(
    root: Path,
    changes: list[PlannedChange],
    *,
    build_command: list[str] | None = None,
    test_command: list[str] | None = None,
) -> None:
    build_command = build_command or [sys.executable, "build_parts_db.py"]
    test_command = test_command or [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-t",
        ".",
    ]
    with tempfile.TemporaryDirectory() as temporary:
        shadow = Path(temporary) / "repo"
        shutil.copytree(
            root,
            shadow,
            ignore=shutil.ignore_patterns(
                ".git",
                "build",
                "result",
                "__pycache__",
                ".direnv",
                ".pytest_cache",
                ".superpowers",
                "*~",
                "*.swp",
                "*.bak",
                ".DS_Store",
                "result-*",
                "fp-info-cache",
                "*-backups",
            ),
        )
        mapped = [
            PlannedChange(shadow / change.path.relative_to(root), change.content)
            for change in changes
        ]
        apply_changes(mapped)
        for command in (build_command, test_command):
            result = subprocess.run(
                command, cwd=shadow, capture_output=True, text=True
            )
            failed = result.returncode != 0
            if not failed and command is build_command:
                built = re.search(
                    r"(?m)^built .+ \(\d+ MPN rows\)\s*$", result.stdout
                )
                warned = any(
                    line.startswith("warning:")
                    for line in result.stderr.splitlines()
                )
                failed = built is None or warned
            if failed:
                raise LcscImportError(
                    "validation failed: "
                    + " ".join(command)
                    + "\n"
                    + result.stdout
                    + "\n"
                    + result.stderr
                )

        kicad_cli = shutil.which("kicad-cli")
        if kicad_cli:
            upgrade_dir = shadow / ".kicad-cli"
            for index, change in enumerate(mapped):
                if (
                    change.content is None
                    or change.path.suffix != ".kicad_sym"
                ):
                    continue
                upgrade_dir.mkdir(parents=True, exist_ok=True)
                output = upgrade_dir / f"{index}-{change.path.name}"
                command = [
                    kicad_cli,
                    "sym",
                    "upgrade",
                    "--force",
                    "--output",
                    str(output),
                    str(change.path),
                ]
                result = subprocess.run(
                    command, cwd=shadow, capture_output=True, text=True
                )
                if result.returncode != 0:
                    raise LcscImportError(
                        "validation failed: "
                        + " ".join(command)
                        + "\n"
                        + result.stdout
                        + "\n"
                        + result.stderr
                    )


def _table_text(root: Path, kind: str) -> tuple[Path, str]:
    if kind == "symbol":
        path = root / "sym-lib-table"
        header = "(sym_lib_table\n\t(version 7)\n)\n"
    else:
        path = root / "fp-lib-table"
        header = "(fp_lib_table\n\t(version 7)\n)\n"
    if path.exists():
        return path, path.read_text(encoding="utf-8")
    return path, header


def build_changes(
    *,
    root: Path,
    doc: dict,
    symbol_lib: str,
    symbol_name: str,
    symbol_block: str,
    footprint_lib: str,
    footprint_name: str,
    footprint_text: str,
    model_path: Path,
    entry: dict,
    replace: bool = False,
) -> list[PlannedChange]:
    symbol_path = root / "symbols" / f"{symbol_lib}.kicad_sym"
    target_symbol_text = (
        symbol_path.read_text(encoding="utf-8")
        if symbol_path.exists()
        else None
    )
    merged = merge_symbol_library(
        target_symbol_text, symbol_name, symbol_block, replace=replace
    )
    changes = [
        PlannedChange(symbol_path, merged.encode("utf-8")),
        PlannedChange(
            root
            / "footprints"
            / f"{footprint_lib}.pretty"
            / f"{footprint_name}.kicad_mod",
            footprint_text.encode("utf-8"),
        ),
        PlannedChange(
            root / "3dmodels" / f"{footprint_name}.step",
            model_path.read_bytes(),
        ),
    ]

    parts_path = root / "parts.yaml"
    changes.append(
        PlannedChange(
            parts_path,
            append_part_yaml_text(
                parts_path.read_text(encoding="utf-8"), entry
            ).encode("utf-8"),
        )
    )

    for kind, nick in (("symbol", symbol_lib), ("footprint", footprint_lib)):
        table_path, table_text = _table_text(root, kind)
        uri = library_uri(kind, nick)
        check_library_registration(table_text, nick, uri)
        updated = append_library_row(
            table_text, nick, uri, "Imported from LCSC"
        )
        changes.append(PlannedChange(table_path, updated.encode("utf-8")))
    return changes


def print_plan(changes: list[PlannedChange]) -> None:
    for change in changes:
        action = "delete" if change.content is None else "write"
        print(f"{action} {change.path}")


def run(args: argparse.Namespace) -> int:
    if args.search:
        print(format_candidates(search_jlcpcb(args.mpn)))
        return 0

    missing = [
        flag
        for flag, value in (
            ("--symbol-lib", args.symbol_lib),
            ("--footprint-lib", args.footprint_lib),
        )
        if value is None
    ]
    if missing:
        raise LcscImportError(
            f"{', '.join(missing)} required unless --search is used"
        )

    root = args.root.resolve()
    validate_name(args.symbol_lib, "symbol library")
    validate_name(args.footprint_lib, "footprint library")
    parts_path = root / "parts.yaml"
    if not parts_path.is_file():
        raise LcscImportError(f"{parts_path} not found")

    doc = yaml.safe_load(parts_path.read_text(encoding="utf-8")) or {}
    candidates = search_jlcpcb(args.mpn)

    if args.lcsc_id:
        selected = next(
            (row for row in candidates if row["lcsc"] == args.lcsc_id), None
        )
        if selected is None:
            selected = {
                "lcsc": args.lcsc_id,
                "mpn": args.mpn,
                "manufacturer": "",
                "package": "",
                "category": "",
                "description": "",
                "name": "",
                "datasheet": "",
                "stock": 0,
                "type": "",
                "url": "",
            }
    else:
        try:
            selected = select_exact_mpn(args.mpn, candidates)
        except LcscImportError:
            print(format_candidates(candidates))
            raise

    with tempfile.TemporaryDirectory() as temporary:
        stem = Path(temporary) / args.symbol_lib
        run_easyeda2kicad(
            selected["lcsc"], stem, args.easyeda2kicad
        )
        outputs = generated_outputs(stem)
        generated_name, generated_block = extract_single_symbol(
            outputs["symbol"]
        )
        symbol_name = validate_name(
            args.symbol_name or generated_name, "symbol"
        )
        symbol_block = rename_symbol(
            generated_block, generated_name, symbol_name
        )
        generated_footprint = read_single_footprint(outputs["pretty"])
        footprint_name = validate_name(
            args.footprint_name or generated_footprint.stem,
            "footprint",
        )
        model_path = read_single_step(outputs["shapes"])
        footprint_text = transform_footprint(
            generated_footprint.read_text(encoding="utf-8"),
            generated_footprint.stem,
            footprint_name,
            footprint_name,
        )

        generated_props = symbol_properties_from_block(symbol_block)
        description = one_line(
            selected["description"]
            or selected["name"]
            or generated_props.get("Description", "")
            or args.mpn
        )
        datasheet = (
            selected["datasheet"]
            or generated_props.get("Datasheet", "")
        )
        keywords = build_keywords(
            generated_props.get("Keywords", ""),
            generated_props.get("ki_keywords", ""),
            selected.get("category", ""),
            args.mpn,
            selected.get("manufacturer", ""),
        )
        symbol_block = add_symbol_metadata(
            symbol_block,
            {
                "Value": args.mpn,
                "Description": description,
                "Datasheet": datasheet,
                "Keywords": keywords,
                "Footprint": f"{args.footprint_lib}:{footprint_name}",
            },
        )
        part_id = args.part_id or args.mpn
        entry = build_part_entry(
            mpn=args.mpn,
            lcsc=selected,
            symbol_lib=args.symbol_lib,
            symbol_name=symbol_name,
            footprint_lib=args.footprint_lib,
            footprint_name=footprint_name,
            package=selected.get("package", ""),
            symbol_props={
                "Description": description,
                "Datasheet": datasheet,
                "Keywords": keywords,
            },
            part_id=part_id,
            height_mm=args.height_mm,
        )
        check_collisions(
            root=root,
            symbol_lib=args.symbol_lib,
            symbol_name=symbol_name,
            footprint_lib=args.footprint_lib,
            footprint_name=footprint_name,
            model_name=footprint_name,
            doc=doc,
            part_id=part_id,
            mpn=args.mpn,
            replace=args.replace,
        )
        changes = build_changes(
            root=root,
            doc=doc,
            symbol_lib=args.symbol_lib,
            symbol_name=symbol_name,
            symbol_block=symbol_block,
            footprint_lib=args.footprint_lib,
            footprint_name=footprint_name,
            footprint_text=footprint_text,
            model_path=model_path,
            entry=entry,
            replace=args.replace,
        )

    if args.replace:
        for change in changes:
            if (
                change.content is not None
                and change.path.suffix
                in {".kicad_sym", ".kicad_mod", ".step", ".stp"}
                and change.path.exists()
            ):
                print(f"replacing {change.path}", file=sys.stderr)

    if args.dry_run:
        print_plan(changes)
        return 0

    validate_in_shadow(root, changes)
    apply_changes(changes)
    print(
        f"imported {args.mpn} ({selected['lcsc']}) as {part_id} "
        f"into {args.symbol_lib}:{symbol_name}"
    )
    if args.height_mm is None:
        print(
            "note: height_mm was not recorded; add it later from the "
            "datasheet or 3D model"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one part by MPN from JLCPCB/LCSC"
    )
    parser.add_argument(
        "mpn", help="exact manufacturer part number to look up"
    )
    parser.add_argument(
        "--symbol-lib",
        help="symbol library nickname (required unless --search)",
    )
    parser.add_argument(
        "--footprint-lib",
        help="footprint library nickname (required unless --search)",
    )
    parser.add_argument(
        "--symbol-name",
        help="symbol name (default: generated name)",
    )
    parser.add_argument(
        "--footprint-name",
        help="footprint name (default: generated name)",
    )
    parser.add_argument(
        "--part-id", help="parts.yaml part id (default: the MPN)"
    )
    parser.add_argument(
        "--height-mm",
        type=float,
        help="package height in millimetres, recorded on the MPN",
    )
    parser.add_argument(
        "--lcsc-id",
        help="LCSC part number (e.g. C123) to bypass MPN resolution",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="list up to 100 candidates without writing anything",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned changes without applying them",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "overwrite existing symbol, footprint and 3D-model files; "
            "does not bypass parts.yaml id or MPN collisions"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--easyeda2kicad",
        default="easyeda2kicad",
        help="easyeda2kicad executable to run (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except LcscImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
