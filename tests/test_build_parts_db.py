import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from build_parts_db import (
    PartsError,
    flatten_parts,
    load_parts,
    read_symbol_properties,
)

DOC = {
    "version": 1,
    "parts": [
        {
            "id": "DEMO_MCU",
            "symbol": "Test_MCUs:DEMO_MCU",
            "value": "DEMO_MCU",
            "description": "Test MCU",
            "datasheet": "https://example.test/family.pdf",
            "keywords": "DEMO_MCU DEMO-MCU",
            "footprints": [
                {
                    "footprint": "Test:PKG-A",
                    "package": "PKG-A",
                    "mpns": [
                        {
                            "mpn": "DEMO-MCU-A",
                            "manufacturer": "Example",
                            "height_mm": 1.6,
                        },
                        {
                            "mpn": "DEMO-MCU-B",
                            "manufacturer": "Example",
                            "height_mm": 1.4,
                            "description": "MCU 256KB",
                            "datasheet": "https://example.test/b.pdf",
                        },
                    ],
                },
                {
                    "footprint": "Test:PKG-B",
                    "package": "PKG-B",
                    "mpns": [{"mpn": "DEMO-MCU-C", "manufacturer": "Example"}],
                },
            ],
        }
    ],
}


def write_symbol(root: Path, nickname: str, name: str, properties: dict) -> None:
    (root / "symbols").mkdir(parents=True, exist_ok=True)
    body = "".join(
        f'\t\t(property "{key}" "{value}")\n' for key, value in properties.items()
    )
    (root / "symbols" / f"{nickname}.kicad_sym").write_text(
        f'(kicad_symbol_lib\n\t(symbol "{name}"\n{body}\t)\n)\n',
        encoding="utf-8",
    )


SYMBOL_FIELDS = {
    "Value": "SYMBOL_VALUE",
    "Description": "Symbol description (with parens)",
    "Datasheet": "https://example.test/symbol.pdf",
    "Footprint": "Test:SYMBOL-FP",
    "Keywords": "SYM KEY WORDS",
}


def inherited_doc(**overrides):
    part = {
        "id": "P",
        "symbol": "Test_MCUs:PART",
        "footprints": [{"package": "PKG", "mpns": [{"mpn": "P-1"}]}],
    }
    part.update(overrides)
    return {"version": 1, "parts": [part]}


class SymbolPropertyTests(unittest.TestCase):
    def test_reads_direct_properties_of_named_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            path = root / "symbols" / "Test_MCUs.kicad_sym"
            path.write_text(
                path.read_text(encoding="utf-8")
                + '\t(symbol "PART_1_1"\n'
                '\t\t(property "Value" "NESTED")\n'
                "\t)\n)\n",
                encoding="utf-8",
            )
            properties = read_symbol_properties(path, "PART")
            self.assertEqual(properties["Value"], "SYMBOL_VALUE")
            self.assertEqual(
                properties["Description"], "Symbol description (with parens)"
            )

    def test_returns_none_for_missing_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            path = root / "symbols" / "Test_MCUs.kicad_sym"
            self.assertIsNone(read_symbol_properties(path, "OTHER"))


class FlattenTests(unittest.TestCase):
    def test_one_row_per_mpn(self):
        rows = flatten_parts(DOC)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["mpn"] for row in rows],
            ["DEMO-MCU-A", "DEMO-MCU-B", "DEMO-MCU-C"],
        )

    def test_key_is_mpn(self):
        rows = flatten_parts(DOC)
        self.assertEqual(rows[0]["key"], "DEMO-MCU-A")

    def test_part_fields_are_inherited(self):
        row = flatten_parts(DOC)[0]
        self.assertEqual(row["part_id"], "DEMO_MCU")
        self.assertEqual(row["symbol"], "Test_MCUs:DEMO_MCU")
        self.assertEqual(row["value"], "DEMO_MCU")
        self.assertEqual(row["description"], "Test MCU")
        self.assertEqual(row["keywords"], "DEMO_MCU DEMO-MCU")

    def test_mpn_overrides_description_and_datasheet(self):
        row = flatten_parts(DOC)[1]
        self.assertEqual(row["description"], "MCU 256KB")
        self.assertEqual(row["datasheet"], "https://example.test/b.pdf")

    def test_footprint_fields(self):
        rows = flatten_parts(DOC)
        self.assertEqual(rows[0]["footprint"], "Test:PKG-A")
        self.assertEqual(rows[0]["package"], "PKG-A")
        self.assertEqual(rows[2]["footprint"], "Test:PKG-B")

    def test_height_is_per_mpn(self):
        rows = flatten_parts(DOC)
        self.assertEqual(rows[0]["height_mm"], 1.6)
        self.assertEqual(rows[1]["height_mm"], 1.4)
        self.assertIsNone(rows[2]["height_mm"])

    def test_missing_optional_fields_become_empty_strings(self):
        doc = {
            "version": 1,
            "parts": [
                {
                    "id": "X",
                    "symbol": "Shared:X",
                    "footprints": [
                        {
                            "footprint": "Shared:X",
                            "mpns": [{"mpn": "X-1"}],
                        }
                    ],
                }
            ],
        }
        row = flatten_parts(doc)[0]
        self.assertEqual(row["value"], "")
        self.assertEqual(row["manufacturer"], "")
        self.assertEqual(row["package"], "")
        self.assertIsNone(row["height_mm"])

    def test_real_parts_yaml_package_is_text(self):
        doc = load_parts(Path("parts.yaml"))
        self.assertEqual(flatten_parts(doc)[0]["package"], "LQFP-64")

    def test_real_parts_yaml_height_is_numeric(self):
        doc = load_parts(Path("parts.yaml"))
        self.assertEqual(flatten_parts(doc)[0]["height_mm"], 1.6)

    def flatten_with_stderr(self, doc, root):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rows = flatten_parts(doc, root)
        return rows, stderr.getvalue()

    def test_missing_yaml_fields_inherit_from_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            rows, stderr = self.flatten_with_stderr(inherited_doc(), root)
            row = rows[0]
            self.assertEqual(row["value"], "SYMBOL_VALUE")
            self.assertEqual(row["description"], "Symbol description (with parens)")
            self.assertEqual(row["datasheet"], "https://example.test/symbol.pdf")
            self.assertEqual(row["keywords"], "SYM KEY WORDS")
            self.assertEqual(row["footprint"], "Test:SYMBOL-FP")
            self.assertEqual(stderr, "")

    def test_yaml_values_override_symbol_and_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            doc = inherited_doc(
                value="YAML_VALUE",
                description="YAML description",
                datasheet="https://example.test/yaml.pdf",
                keywords="YAML KEYWORDS",
            )
            rows, stderr = self.flatten_with_stderr(doc, root)
            row = rows[0]
            self.assertEqual(row["value"], "YAML_VALUE")
            self.assertEqual(row["description"], "YAML description")
            self.assertEqual(row["datasheet"], "https://example.test/yaml.pdf")
            self.assertEqual(row["keywords"], "YAML KEYWORDS")
            for field in ("value", "description", "datasheet", "keywords"):
                self.assertIn(f"{field} differs from symbol", stderr)

    def test_mpn_overrides_do_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            doc = inherited_doc(
                description="Symbol description (with parens)",
                datasheet="https://example.test/symbol.pdf",
                footprints=[
                    {
                        "package": "PKG",
                        "mpns": [
                            {
                                "mpn": "P-1",
                                "description": "MPN description",
                                "datasheet": "https://example.test/mpn.pdf",
                            }
                        ],
                    }
                ],
            )
            rows, stderr = self.flatten_with_stderr(doc, root)
            self.assertEqual(rows[0]["description"], "MPN description")
            self.assertEqual(rows[0]["datasheet"], "https://example.test/mpn.pdf")
            self.assertEqual(stderr, "")

    def test_single_footprint_mismatch_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            doc = inherited_doc(
                footprints=[
                    {
                        "footprint": "Test:OTHER-FP",
                        "package": "PKG",
                        "mpns": [{"mpn": "P-1"}],
                    }
                ]
            )
            rows, stderr = self.flatten_with_stderr(doc, root)
            self.assertEqual(rows[0]["footprint"], "Test:OTHER-FP")
            self.assertIn("footprint differs from symbol", stderr)

    def test_multiple_footprints_do_not_warn_for_footprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_symbol(root, "Test_MCUs", "PART", SYMBOL_FIELDS)
            doc = inherited_doc(
                footprints=[
                    {
                        "footprint": "Test:FP-A",
                        "package": "A",
                        "mpns": [{"mpn": "P-1"}],
                    },
                    {
                        "footprint": "Test:FP-B",
                        "package": "B",
                        "mpns": [{"mpn": "P-2"}],
                    },
                ]
            )
            rows, stderr = self.flatten_with_stderr(doc, root)
            self.assertEqual([row["footprint"] for row in rows], ["Test:FP-A", "Test:FP-B"])
            self.assertEqual(stderr, "")


class LoadTests(unittest.TestCase):
    def test_rejects_unsupported_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parts.yaml"
            path.write_text("version: 2\nparts: []\n", encoding="utf-8")
            with self.assertRaises(PartsError):
                load_parts(path)

    def test_rejects_empty_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parts.yaml"
            path.write_text("version: 1\nparts: []\n", encoding="utf-8")
            with self.assertRaises(PartsError):
                load_parts(path)

    def test_loads_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parts.yaml"
            path.write_text("version: 1\nparts: [{id: X}]\n", encoding="utf-8")
            doc = load_parts(path)
            self.assertEqual(doc["parts"], [{"id": "X"}])


from build_parts_db import validate_parts


def make_fixture_repo(root: Path, *, with_model: bool = True) -> None:
    (root / "symbols").mkdir(parents=True)
    (root / "footprints" / "Shared.pretty").mkdir(parents=True)
    (root / "3dmodels").mkdir(parents=True)
    write_symbol(
        root,
        "Shared_Resistors",
        "R_0603",
        {
            "Reference": "R",
            "Value": "R_0603",
            "Footprint": "Shared:R_0603",
            "Datasheet": "https://example.test/r0603.pdf",
            "Description": "Resistor 0603",
            "Keywords": "R resistor 0603",
        },
    )
    model = (
        '\n\t(model "${KICAD_LIBS}/3dmodels/R_0603.step")'
        if with_model
        else ""
    )
    (root / "footprints" / "Shared.pretty" / "R_0603.kicad_mod").write_text(
        f'(footprint "R_0603"{model}\n)\n', encoding="utf-8"
    )
    if with_model:
        (root / "3dmodels" / "R_0603.step").write_text("STEP", encoding="utf-8")


def valid_doc():
    return {
        "version": 1,
        "parts": [
            {
                "id": "R_0603_10k",
                "symbol": "Shared_Resistors:R_0603",
                "footprints": [
                    {
                        "footprint": "Shared:R_0603",
                        "mpns": [{"mpn": "RC0603FR-0710KL"}],
                    }
                ],
            }
        ],
    }


class ValidationTests(unittest.TestCase):
    def test_valid_document_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            validate_parts(valid_doc(), root)

    def test_height_mm_accepts_positive_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"][0]["footprints"][0]["mpns"][0]["height_mm"] = 1.6
            validate_parts(doc, root)

    def test_invalid_height_mm_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            for value in (-1, 0, "1.6", True, float("nan"), float("inf")):
                with self.subTest(value=value):
                    doc = valid_doc()
                    doc["parts"][0]["footprints"][0]["mpns"][0]["height_mm"] = value
                    with self.assertRaisesRegex(PartsError, "height_mm"):
                        validate_parts(doc, root)

    def test_missing_symbol_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"][0]["symbol"] = "Shared_Resistors:DOES_NOT_EXIST"
            with self.assertRaisesRegex(PartsError, "symbol"):
                validate_parts(doc, root)

    def test_wrong_symbol_nickname_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"][0]["symbol"] = "WrongLib:R_0603"
            with self.assertRaisesRegex(PartsError, "symbol"):
                validate_parts(doc, root)

    def test_missing_footprint_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"][0]["footprints"][0]["footprint"] = "Shared:DOES_NOT_EXIST"
            with self.assertRaisesRegex(PartsError, "footprint"):
                validate_parts(doc, root)

    def test_footprint_falls_back_to_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            del doc["parts"][0]["footprints"][0]["footprint"]
            validate_parts(doc, root)

    def test_missing_footprint_without_symbol_footprint_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            (root / "symbols" / "Shared_Resistors.kicad_sym").write_text(
                '(kicad_symbol_lib\n'
                '\t(symbol "R_0603"\n'
                '\t\t(property "Reference" "R")\n'
                '\t)\n'
                ')\n',
                encoding="utf-8",
            )
            doc = valid_doc()
            del doc["parts"][0]["footprints"][0]["footprint"]
            with self.assertRaisesRegex(PartsError, "footprint"):
                validate_parts(doc, root)

    def test_missing_3d_model_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root, with_model=False)
            (root / "footprints" / "Shared.pretty" / "R_0603.kicad_mod").write_text(
                '(footprint "R_0603"\n\t(model "${KICAD_LIBS}/3dmodels/missing.step")\n)\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PartsError, "3D model"):
                validate_parts(valid_doc(), root)

    def test_duplicate_mpn_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"].append(
                {
                    "id": "R_0603_10k_alt",
                    "symbol": "Shared_Resistors:R_0603",
                    "footprints": [
                        {
                            "footprint": "Shared:R_0603",
                            "mpns": [{"mpn": "RC0603FR-0710KL"}],
                        }
                    ],
                }
            )
            with self.assertRaisesRegex(PartsError, "duplicate MPN"):
                validate_parts(doc, root)

    def test_duplicate_part_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"].append(
                {
                    "id": "R_0603_10k",
                    "symbol": "Shared_Resistors:R_0603",
                    "footprints": [
                        {
                            "footprint": "Shared:R_0603",
                            "mpns": [{"mpn": "OTHER-MPN"}],
                        }
                    ],
                }
            )
            with self.assertRaisesRegex(PartsError, "duplicate part id"):
                validate_parts(doc, root)

    def test_missing_footprints_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"][0]["footprints"] = []
            with self.assertRaisesRegex(PartsError, "no footprints"):
                validate_parts(doc, root)

    def test_missing_mpn_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            doc = valid_doc()
            doc["parts"][0]["footprints"][0]["mpns"] = []
            with self.assertRaisesRegex(PartsError, "no MPNs"):
                validate_parts(doc, root)

    def test_repository_parts_yaml_is_valid(self):
        root = Path(__file__).resolve().parent.parent
        validate_parts(load_parts(root / "parts.yaml"), root)


import sqlite3

from build_parts_db import DB_FIELDS, build, main, write_sqlite


class SqliteTests(unittest.TestCase):
    def test_write_sqlite_creates_rows_and_meta(self):
        rows = [
            {
                "key": "A",
                "part_id": "P",
                "symbol": "Shared:X",
                "value": "V",
                "description": "D",
                "keywords": "K",
                "datasheet": "U",
                "footprint": "Shared:F",
                "package": "PKG",
                "height_mm": 1.6,
                "mpn": "A",
                "manufacturer": "M",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "parts.sqlite"
            write_sqlite(rows, output, "abc123")
            conn = sqlite3.connect(output)
            try:
                columns = [
                    row[1] for row in conn.execute("PRAGMA table_info(parts)")
                ]
                self.assertEqual(columns, DB_FIELDS)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute("SELECT height_mm FROM parts").fetchone()[0], 1.6
                )
                meta = conn.execute(
                    "SELECT schema_version, generator, source_commit FROM _meta"
                ).fetchone()
                self.assertEqual(meta, (2, "build_parts_db.py", "abc123"))
            finally:
                conn.close()

    def test_write_sqlite_failure_preserves_existing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "parts.sqlite"
            original_rows = [
                {
                    "key": "ORIGINAL",
                    "part_id": "P",
                    "symbol": "Shared:X",
                    "value": "V",
                    "description": "D",
                    "keywords": "K",
                    "datasheet": "U",
                    "footprint": "Shared:F",
                    "package": "PKG",
                    "height_mm": 1.6,
                    "mpn": "ORIGINAL",
                    "manufacturer": "M",
                }
            ]
            write_sqlite(original_rows, output, "abc123")

            duplicate_rows = [
                dict(original_rows[0], key="DUP"),
                dict(original_rows[0], key="DUP"),
            ]
            with self.assertRaises(sqlite3.IntegrityError):
                write_sqlite(duplicate_rows, output, "deadbeef")

            conn = sqlite3.connect(output)
            try:
                rows = conn.execute("SELECT key, mpn FROM parts").fetchall()
                self.assertEqual(rows, [("ORIGINAL", "ORIGINAL")])
                meta = conn.execute("SELECT source_commit FROM _meta").fetchone()
                self.assertEqual(meta, ("abc123",))
            finally:
                conn.close()

    def test_build_writes_database_from_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            parts = root / "parts.yaml"
            parts.write_text(
                "version: 1\n"
                "parts:\n"
                "  - id: R_0603_10k\n"
                "    symbol: Shared_Resistors:R_0603\n"
                "    value: R_0603\n"
                "    footprints:\n"
                "      - footprint: Shared:R_0603\n"
                "        package: 0603\n"
                "        mpns:\n"
                "          - mpn: RC0603FR-0710KL\n"
                "            manufacturer: Yageo\n"
                "            height_mm: 0.45\n",
                encoding="utf-8",
            )
            output = root / "build" / "parts.sqlite"
            rows = build(parts, output, root, "deadbeef")
            self.assertEqual(len(rows), 1)
            self.assertTrue(output.is_file())
            conn = sqlite3.connect(output)
            try:
                row = conn.execute(
                    "SELECT key, symbol, footprint, mpn, height_mm FROM parts"
                ).fetchone()
                self.assertEqual(
                    row,
                    (
                        "RC0603FR-0710KL",
                        "Shared_Resistors:R_0603",
                        "Shared:R_0603",
                        "RC0603FR-0710KL",
                        0.45,
                    ),
                )
            finally:
                conn.close()


    def test_build_inherits_fields_from_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            parts = root / "parts.yaml"
            parts.write_text(
                "version: 1\n"
                "parts:\n"
                "  - id: R_0603_10k\n"
                "    symbol: Shared_Resistors:R_0603\n"
                "    footprints:\n"
                "      - package: 0603\n"
                "        mpns:\n"
                "          - mpn: RC0603FR-0710KL\n",
                encoding="utf-8",
            )
            output = root / "build" / "parts.sqlite"
            build(parts, output, root, "deadbeef")
            conn = sqlite3.connect(output)
            try:
                row = conn.execute(
                    "SELECT value, description, keywords, datasheet, footprint, "
                    "height_mm FROM parts"
                ).fetchone()
                self.assertEqual(
                    row,
                    (
                        "R_0603",
                        "Resistor 0603",
                        "R resistor 0603",
                        "https://example.test/r0603.pdf",
                        "Shared:R_0603",
                        None,
                    ),
                )
            finally:
                conn.close()


class CliTests(unittest.TestCase):
    def test_main_writes_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            parts = root / "parts.yaml"
            parts.write_text(
                "version: 1\n"
                "parts:\n"
                "  - id: R_0603_10k\n"
                "    symbol: Shared_Resistors:R_0603\n"
                "    footprints:\n"
                "      - footprint: Shared:R_0603\n"
                "        mpns:\n"
                "          - mpn: RC0603FR-0710KL\n",
                encoding="utf-8",
            )
            output = root / "build" / "parts.sqlite"
            self.assertEqual(
                main(
                    [
                        "--parts",
                        str(parts),
                        "--output",
                        str(output),
                        "--root",
                        str(root),
                        "--source-commit",
                        "cafe",
                    ]
                ),
                0,
            )
            self.assertTrue(output.is_file())

    def test_main_returns_one_on_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_repo(root)
            parts = root / "parts.yaml"
            parts.write_text("version: 1\nparts: []\n", encoding="utf-8")
            self.assertEqual(
                main(
                    [
                        "--parts",
                        str(parts),
                        "--output",
                        str(root / "build" / "parts.sqlite"),
                        "--root",
                        str(root),
                    ]
                ),
                1,
            )


class KiCadConfigTests(unittest.TestCase):
    FIELD_KEYS = {
        "column",
        "name",
        "visible_on_add",
        "visible_in_chooser",
        "show_name",
        "inherit_properties",
    }

    @staticmethod
    def parse_lib_table(text):
        entries = {}
        pattern = re.compile(
            r'\(lib\s+\(name\s+"([^"]+)"\)[^\n]*?\(uri\s+"([^"]+)"\)'
        )
        for name, uri in pattern.findall(text):
            entries[name] = uri
        return entries

    def test_kicad_dbl_is_valid_json_and_references_columns(self):
        config = json.loads(Path("parts.kicad_dbl").read_text(encoding="utf-8"))

        self.assertEqual(config["meta"], {"version": 1})
        self.assertNotIn("tables", config)
        self.assertIn("libraries", config)

        library = config["libraries"][0]
        self.assertEqual(library["name"], "Parts")
        self.assertEqual(library["table"], "parts")
        self.assertEqual(library["key"], "key")
        self.assertEqual(library["symbols"], "symbol")
        self.assertEqual(library["footprints"], "footprint")
        self.assertEqual(library["properties"]["description"], "description")
        self.assertEqual(library["properties"]["keywords"], "keywords")

        columns = {field["column"] for field in library["fields"]}
        self.assertLessEqual(
            {"value", "datasheet", "mpn", "manufacturer", "height_mm"}, columns
        )
        height_field = next(
            field for field in library["fields"] if field["column"] == "height_mm"
        )
        self.assertEqual(height_field["name"], "Height (mm)")
        for field in library["fields"]:
            self.assertLessEqual(set(field), self.FIELD_KEYS)
            self.assertNotIn("type", field)

        self.assertEqual(config["source"]["type"], "odbc")
        connection_string = config["source"]["connection_string"]
        self.assertIn("${CWD}/build/parts.sqlite", connection_string)
        self.assertIn("Driver={SQLite3}", connection_string)

    def test_shared_symbol_table_registers_database_library(self):
        text = Path("sym-lib-table").read_text(encoding="utf-8")
        self.assertIn('(name "SharedDB")', text)
        self.assertIn('(type "Database")', text)
        self.assertIn("parts.kicad_dbl", text)

    def test_parts_yaml_library_ids_have_matching_table_entries(self):
        symbol_libraries = self.parse_lib_table(
            Path("sym-lib-table").read_text(encoding="utf-8")
        )
        footprint_libraries = self.parse_lib_table(
            Path("fp-lib-table").read_text(encoding="utf-8")
        )
        rows = flatten_parts(load_parts(Path("parts.yaml")))

        symbol_nicknames = {row["symbol"].split(":", 1)[0] for row in rows}
        footprint_nicknames = {row["footprint"].split(":", 1)[0] for row in rows}

        for nickname in symbol_nicknames:
            self.assertIn(nickname, symbol_libraries)
            self.assertTrue(
                symbol_libraries[nickname].endswith(f"/{nickname}.kicad_sym"),
                symbol_libraries[nickname],
            )

        for nickname in footprint_nicknames:
            self.assertIn(nickname, footprint_libraries)
            self.assertTrue(
                footprint_libraries[nickname].endswith(f"/{nickname}.pretty"),
                footprint_libraries[nickname],
            )

        self.assertTrue(
            footprint_libraries["Shared"].endswith("/Shared.pretty")
        )
