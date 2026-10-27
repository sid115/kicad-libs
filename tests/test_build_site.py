import contextlib
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from build_parts_db import PartsError, load_parts
from build_site import (
    build,
    main,
    sanitise_datasheet,
    site_rows,
)
from tests.helpers import make_fixture_repo


SITE_DOC = {
    "version": 1,
    "parts": [
        {
            "id": "DEMO_MCU",
            "symbol": "Test_MCUs:DEMO_MCU",
            "value": 'DEMO <MCU> & "friends"',
            "description": "München CPU",
            "datasheet": "https://example.test/family.pdf",
            "keywords": "demo",
            "footprints": [
                {
                    "footprint": "Test:PKG-A",
                    "package": "PKG-A",
                    "mpns": [
                        {
                            "mpn": "DEMO-1",
                            "manufacturer": "Acme",
                            "height_mm": 1.6,
                        },
                        {
                            "mpn": "DEMO-2",
                            "manufacturer": "Acme",
                            "description": "MCU 256KB <variant>",
                            "datasheet": "javascript:alert(1)",
                        },
                    ],
                }
            ],
        }
    ],
}

EXPECTED_KEYS = {
    "part_id",
    "value",
    "description",
    "mpn",
    "manufacturer",
    "package",
    "datasheet",
}

STRING_FIELDS = (
    "part_id",
    "value",
    "description",
    "mpn",
    "manufacturer",
    "package",
)


class SiteRowsTests(unittest.TestCase):
    def test_one_object_per_mpn_with_exact_keys(self):
        rows = site_rows(SITE_DOC)
        self.assertEqual([row["mpn"] for row in rows], ["DEMO-1", "DEMO-2"])
        for row in rows:
            self.assertEqual(set(row), EXPECTED_KEYS)

    def test_string_fields_are_strings(self):
        for row in site_rows(SITE_DOC):
            for field in STRING_FIELDS:
                self.assertIsInstance(row[field], str, field)

    def test_non_string_package_raises_parts_error(self):
        doc = copy.deepcopy(SITE_DOC)
        doc["parts"][0]["footprints"][0]["package"] = 387
        with self.assertRaisesRegex(PartsError, "package"):
            site_rows(doc)

    def test_inherits_part_metadata_and_applies_mpn_overrides(self):
        first, second = site_rows(SITE_DOC)
        self.assertEqual(first["part_id"], "DEMO_MCU")
        self.assertEqual(first["value"], 'DEMO <MCU> & "friends"')
        self.assertEqual(first["description"], "München CPU")
        self.assertEqual(first["package"], "PKG-A")
        self.assertEqual(second["description"], "MCU 256KB <variant>")

    def test_sanitises_datasheet_per_row(self):
        rows = site_rows(SITE_DOC)
        self.assertEqual(rows[0]["datasheet"], "https://example.test/family.pdf")
        self.assertIsNone(rows[1]["datasheet"])

    def test_preserves_special_characters(self):
        payload = json.dumps(site_rows(SITE_DOC), ensure_ascii=False)
        round_tripped = json.loads(payload)
        self.assertEqual(round_tripped[1]["description"], "MCU 256KB <variant>")


class SanitiseDatasheetTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        for value in ("https://example.test/a.pdf", "HTTP://example.test/a.pdf"):
            self.assertEqual(sanitise_datasheet(value), value)

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(
            sanitise_datasheet("  https://example.test/a.pdf  "),
            "https://example.test/a.pdf",
        )

    def test_rejects_unsupported_values(self):
        for value in (
            "javascript:alert(1)",
            "data:text/html,hello",
            "//example.test/a.pdf",
            "/a.pdf",
            "a.pdf",
            "",
            "   ",
            "~",
            "https://",
            "http:///path",
            None,
            123,
        ):
            self.assertIsNone(sanitise_datasheet(value), value)

    def test_rejects_malformed_or_hostless_urls(self):
        for value in (
            "http://[::1",
            "https://exa／mple.com",
            "https://user@/path",
            "https://:8080/path",
        ):
            self.assertIsNone(sanitise_datasheet(value), value)


SITE_DIR = Path(__file__).resolve().parents[1] / "site"


class MarkupTests(unittest.TestCase):
    def test_assets_exist(self):
        for name in ("index.html", "app.js", "style.css"):
            self.assertTrue((SITE_DIR / name).is_file(), name)

    def test_app_js_element_ids_exist_in_html(self):
        html = (SITE_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (SITE_DIR / "app.js").read_text(encoding="utf-8")
        referenced = set(re.findall(r'getElementById\("([^"]+)"\)', app_js))
        declared = set(re.findall(r'\bid="([^"]+)"', html))
        self.assertTrue(referenced)
        self.assertEqual(referenced - declared, set())

    def test_index_uses_relative_assets(self):
        html = (SITE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="style.css"', html)
        self.assertIn('src="app.js"', html)
        self.assertNotIn('href="/', html)
        self.assertNotIn('src="/', html)

    def test_table_has_one_column_per_displayed_field_starting_with_mpn(self):
        html = (SITE_DIR / "index.html").read_text(encoding="utf-8")
        headers = re.findall(r'<th scope="col" data-key="([^"]+)"', html)
        self.assertEqual(
            headers,
            [
                "mpn",
                "description",
                "manufacturer",
                "package",
                "datasheet",
            ],
        )
        self.assertEqual(
            re.findall(r'data-key="([^"]+)" aria-sort="ascending"', html),
            ["mpn"],
        )
        self.assertIn('colspan="5"', html)


def write_site_fixture(root: Path) -> Path:
    make_fixture_repo(root)
    parts = root / "parts.yaml"
    parts.write_text(
        """version: 1
parts:
  - id: R_0603_10k
    symbol: Shared_Resistors:R_0603
    value: R_0603
    description: Resistor 0603
    datasheet: https://example.test/r0603.pdf
    keywords: R resistor 0603
    footprints:
      - footprint: Shared:R_0603
        package: "0603"
        mpns:
          - mpn: RC0603FR-0710KL
            manufacturer: Yageo
            height_mm: 0.45
""",
        encoding="utf-8",
    )
    return parts


class WriteSiteTests(unittest.TestCase):
    def test_writes_json_and_copies_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = write_site_fixture(root)
            output = root / "out"
            rows = build(parts, output, root)
            self.assertEqual(len(rows), 1)
            data = json.loads((output / "parts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["mpn"], "RC0603FR-0710KL")
            self.assertEqual(data[0]["package"], "0603")
            self.assertNotIn("height_mm", data[0])
            for name in ("index.html", "app.js", "style.css"):
                self.assertEqual(
                    (output / name).read_bytes(),
                    (SITE_DIR / name).read_bytes(),
                    name,
                )

    def test_rerun_refreshes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = write_site_fixture(root)
            output = root / "out"
            build(parts, output, root)
            (output / "parts.json").write_text("stale", encoding="utf-8")
            build(parts, output, root)
            data = json.loads((output / "parts.json").read_text(encoding="utf-8"))
            self.assertEqual(data[0]["mpn"], "RC0603FR-0710KL")


class FailureTests(unittest.TestCase):
    def test_invalid_parts_yaml_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = root / "parts.yaml"
            parts.write_text("version: 1\nparts: []\n", encoding="utf-8")
            output = root / "out"
            with self.assertRaises(PartsError):
                build(parts, output, root)
            self.assertFalse(output.exists())

    def test_invalid_parts_yaml_leaves_existing_output_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = root / "parts.yaml"
            parts.write_text("version: 1\nparts: []\n", encoding="utf-8")
            output = root / "out"
            output.mkdir()
            sentinel = output / "parts.json"
            sentinel.write_text("old", encoding="utf-8")
            with self.assertRaises(PartsError):
                build(parts, output, root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "old")


class CliTests(unittest.TestCase):
    def test_prints_built_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = write_site_fixture(root)
            output = root / "out"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--parts",
                        str(parts),
                        "--root",
                        str(root),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                f"built {output / 'index.html'} (1 MPN rows)",
            )

    def test_default_output_is_relative_to_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = write_site_fixture(root)
            stdout = io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stdout(stdout):
                code = main(["--parts", str(parts), "--root", str(root)])
            self.assertEqual(code, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "built build/site/index.html (1 MPN rows)",
            )
            self.assertTrue((root / "build" / "site" / "index.html").is_file())

    def test_invalid_parts_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = root / "parts.yaml"
            parts.write_text("version: 1\nparts: []\n", encoding="utf-8")
            output = root / "out"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--parts",
                        str(parts),
                        "--root",
                        str(root),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("error:", stderr.getvalue())


class RepositoryTests(unittest.TestCase):
    def test_real_parts_yaml_builds_every_part(self):
        repo = Path(__file__).resolve().parents[1]
        doc = load_parts(repo / "parts.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "site"
            rows = build(repo / "parts.yaml", output, repo)
            data = json.loads((output / "parts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data), len(rows))
            self.assertEqual(
                {row["part_id"] for row in data},
                {part["id"] for part in doc["parts"]},
            )

    def test_real_parts_yaml_string_fields_are_strings(self):
        repo = Path(__file__).resolve().parents[1]
        rows = site_rows(load_parts(repo / "parts.yaml"), repo)
        self.assertTrue(rows)
        for row in rows:
            for field in STRING_FIELDS:
                self.assertIsInstance(row[field], str, field)
