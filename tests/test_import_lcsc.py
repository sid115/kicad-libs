import contextlib
import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml

import import_lcsc
from tests.helpers import make_fixture_repo


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def jlc_payload(rows):
    return {
        "data": {
            "componentPageInfo": {
                "total": len(rows),
                "list": rows,
            }
        }
    }


def jlc_row(code, model, brand="Acme", package="SOT-23", stock=10):
    return {
        "componentCode": code,
        "componentModelEn": model,
        "componentBrandEn": brand,
        "componentSpecificationEn": package,
        "componentTypeEn": "Transistors",
        "stockCount": stock,
        "componentLibraryType": "expand",
        "describe": f"{model} description",
        "componentName": f"{model} name",
        "dataManualUrl": f"https://example.test/{code}.pdf",
        "lcscGoodsUrl": f"https://example.test/{code}",
        "componentPrices": [{"startNumber": 1, "productPrice": 0.1}],
        "attributes": [],
    }


class LookupTests(unittest.TestCase):
    def test_search_maps_and_normalizes_candidates(self):
        payload = jlc_payload([jlc_row("C123", "ABC-1")])
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            results = import_lcsc.search_jlcpcb("ABC-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["lcsc"], "C123")
        self.assertEqual(results[0]["mpn"], "ABC-1")
        self.assertEqual(results[0]["manufacturer"], "Acme")
        self.assertEqual(results[0]["package"], "SOT-23")
        self.assertEqual(results[0]["category"], "Transistors")
        self.assertEqual(results[0]["description"], "ABC-1 description")
        self.assertEqual(results[0]["datasheet"], "https://example.test/C123.pdf")
        self.assertEqual(results[0]["type"], "Extended")

    def test_search_normalizes_null_and_non_string_fields(self):
        null_row = jlc_row("C123", "ABC-1")
        null_row.update(
            {
                "componentModelEn": None,
                "componentBrandEn": None,
                "componentSpecificationEn": None,
                "componentTypeEn": None,
                "stockCount": None,
                "componentLibraryType": None,
                "describe": None,
                "componentName": None,
                "dataManualUrl": None,
                "lcscGoodsUrl": None,
            }
        )
        mixed_row = jlc_row("C124", 1234)
        mixed_row.update(
            {
                "componentBrandEn": 42,
                "stockCount": "7",
                "componentLibraryType": "base",
            }
        )
        payload = jlc_payload([null_row, mixed_row])
        with mock.patch(
            "urllib.request.urlopen", return_value=FakeResponse(payload)
        ):
            results = import_lcsc.search_jlcpcb("ABC-1")
        self.assertEqual(
            results[0],
            {
                "lcsc": "C123",
                "mpn": "",
                "manufacturer": "",
                "package": "",
                "category": "",
                "stock": 0,
                "type": "",
                "description": "",
                "name": "",
                "datasheet": "",
                "url": "",
            },
        )
        self.assertEqual(results[1]["mpn"], "1234")
        self.assertEqual(results[1]["manufacturer"], "42")
        self.assertEqual(results[1]["stock"], 7)
        self.assertEqual(results[1]["type"], "Basic")

    def test_search_deduplicates_by_lcsc_id(self):
        payload = jlc_payload(
            [jlc_row("C123", "ABC-1"), jlc_row("C123", "ABC-1")]
        )
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            results = import_lcsc.search_jlcpcb("ABC-1")
        self.assertEqual([row["lcsc"] for row in results], ["C123"])

    def test_search_reports_http_failure(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "lookup failed"):
                import_lcsc.search_jlcpcb("ABC-1")

    def test_select_accepts_unique_exact_case_insensitive_match(self):
        candidates = [
            {"lcsc": "C1", "mpn": "abc-1"},
            {"lcsc": "C2", "mpn": "ABC-1-SPECIAL"},
        ]
        selected = import_lcsc.select_exact_mpn(" ABC-1 ", candidates)
        self.assertEqual(selected["lcsc"], "C1")

    def test_select_rejects_no_exact_match(self):
        with self.assertRaisesRegex(import_lcsc.LcscImportError, "no exact"):
            import_lcsc.select_exact_mpn("ABC-1", [{"lcsc": "C1", "mpn": "XYZ"}])

    def test_select_rejects_multiple_exact_matches(self):
        candidates = [
            {"lcsc": "C1", "mpn": "ABC-1"},
            {"lcsc": "C2", "mpn": "abc-1"},
        ]
        with self.assertRaisesRegex(import_lcsc.LcscImportError, "multiple exact"):
            import_lcsc.select_exact_mpn("ABC-1", candidates)

    def test_select_ignores_null_mpns(self):
        selected = import_lcsc.select_exact_mpn(
            "ABC-1",
            [
                {"lcsc": "C1", "mpn": None},
                {"lcsc": "C2", "mpn": "ABC-1"},
            ],
        )
        self.assertEqual(selected["lcsc"], "C2")
        with self.assertRaisesRegex(import_lcsc.LcscImportError, "no exact"):
            import_lcsc.select_exact_mpn(
                "ABC-1", [{"lcsc": "C1", "mpn": None}]
            )

    def test_format_candidates_includes_key_fields(self):
        text = import_lcsc.format_candidates(
            [{"lcsc": "C1", "mpn": "ABC", "manufacturer": "Acme",
              "package": "SOT-23", "stock": 12, "type": "Basic"}]
        )
        self.assertIn("C1", text)
        self.assertIn("ABC", text)
        self.assertIn("SOT-23", text)

    def test_format_candidates_emits_at_most_100_rows(self):
        candidates = [jlc_row(f"C{i}", f"MPN-{i}") for i in range(120)]
        text = import_lcsc.format_candidates(candidates)
        self.assertEqual(len(text.splitlines()), 101)

    def test_search_requests_100_rows(self):
        payload = jlc_payload([jlc_row("C123", "ABC-1")])
        with mock.patch(
            "urllib.request.urlopen", return_value=FakeResponse(payload)
        ) as urlopen:
            import_lcsc.search_jlcpcb("ABC-1")
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)["pageSize"], 100)

    def test_format_candidates_tolerates_null_and_non_string_fields(self):
        text = import_lcsc.format_candidates(
            [
                {
                    "lcsc": None,
                    "mpn": 123,
                    "manufacturer": None,
                    "package": None,
                    "stock": None,
                    "type": None,
                }
            ]
        )
        self.assertNotIn("None", text)
        self.assertIn("123", text)
        self.assertRegex(text, r"\s0\s*$")


GENERATED_LIB = """(kicad_symbol_lib
  (version 20211014)
  (symbol "RP2040"
    (property
      "Value"
      "RP2040"
      (id 1)
    )
    (property
      "Footprint"
      "OldLib:LQFN-56"
      (id 2)
    )
    (property
      "Datasheet"
      "https://example.test/old.pdf"
      (id 3)
    )
    (property
      "ki_keywords"
      "Other MCUs"
      (id 4)
    )
    (symbol "RP2040_0_1"
      (rectangle
        (start 0 0)
        (end 1 1)
      )
    )
  )
)
"""


class SymbolTransformTests(unittest.TestCase):
    def test_extract_single_symbol(self):
        path = Path("/tmp/does-not-matter.kicad_sym")
        with mock.patch.object(Path, "read_text", return_value=GENERATED_LIB):
            name, block = import_lcsc.extract_single_symbol(path)
        self.assertEqual(name, "RP2040")
        self.assertIn('(symbol "RP2040_0_1"', block)
        self.assertNotIn("kicad_symbol_lib", block)

    def test_rename_updates_top_level_and_units(self):
        _, block = import_lcsc.extract_single_symbol_from_text(GENERATED_LIB)
        renamed = import_lcsc.rename_symbol(block, "RP2040", "NEWPART")
        self.assertIn('(symbol "NEWPART"', renamed)
        self.assertIn('(symbol "NEWPART_0_1"', renamed)
        self.assertNotIn('"RP2040', renamed)

    def test_set_symbol_property_replaces_multiline_value(self):
        _, block = import_lcsc.extract_single_symbol_from_text(GENERATED_LIB)
        updated = import_lcsc.set_symbol_property(
            block, "Footprint", "Shared:PKG"
        )
        self.assertIn('"Shared:PKG"', updated)
        self.assertNotIn("OldLib:LQFN-56", updated)

    def test_add_symbol_metadata_adds_description_and_maps_keywords(self):
        _, block = import_lcsc.extract_single_symbol_from_text(GENERATED_LIB)
        updated = import_lcsc.add_symbol_metadata(
            block,
            {
                "Value": "NEWPART",
                "Description": "A new part",
                "Datasheet": "https://example.test/new.pdf",
                "Keywords": "NEWPART test",
                "Footprint": "Shared:PKG",
            },
        )
        props = import_lcsc.symbol_properties_from_block(updated)
        self.assertEqual(props["Value"], "NEWPART")
        self.assertEqual(props["Description"], "A new part")
        self.assertEqual(props["Datasheet"], "https://example.test/new.pdf")
        self.assertEqual(props["Keywords"], "NEWPART test")
        self.assertEqual(props["Footprint"], "Shared:PKG")

    def test_merge_preserves_existing_symbol(self):
        existing = (
            '(kicad_symbol_lib\n\t(version 20251024)\n'
            '\t(symbol "OLD"\n\t)\n)\n'
        )
        _, block = import_lcsc.extract_single_symbol_from_text(GENERATED_LIB)
        merged = import_lcsc.merge_symbol_library(existing, "RP2040", block)
        self.assertIn('(symbol "OLD"', merged)
        self.assertIn('(symbol "RP2040"', merged)
        self.assertTrue(merged.rstrip().endswith(")"))

    def test_merge_creates_wrapper_for_new_library(self):
        _, block = import_lcsc.extract_single_symbol_from_text(GENERATED_LIB)
        merged = import_lcsc.merge_symbol_library(None, "RP2040", block)
        self.assertIn("(kicad_symbol_lib", merged)
        self.assertIn('(symbol "RP2040"', merged)

    def test_merge_rejects_duplicate_symbol(self):
        existing = (
            '(kicad_symbol_lib\n\t(version 20251024)\n'
            '\t(symbol "RP2040"\n\t)\n)\n'
        )
        _, block = import_lcsc.extract_single_symbol_from_text(GENERATED_LIB)
        with self.assertRaisesRegex(import_lcsc.LcscImportError, "already exists"):
            import_lcsc.merge_symbol_library(existing, "RP2040", block)


class CadTransformTests(unittest.TestCase):
    def test_validate_name_rejects_path_separators(self):
        for value in ("", ".", "..", "a/b", "a\\b", "a:b"):
            with self.subTest(value=value):
                with self.assertRaises(import_lcsc.LcscImportError):
                    import_lcsc.validate_name(value, "symbol")
        self.assertEqual(
            import_lcsc.validate_name("PKG-1.2", "footprint"), "PKG-1.2"
        )

    def test_generated_outputs(self):
        outputs = import_lcsc.generated_outputs(Path("/tmp/Shared_MCUs"))
        self.assertEqual(outputs["symbol"], Path("/tmp/Shared_MCUs.kicad_sym"))
        self.assertEqual(outputs["pretty"], Path("/tmp/Shared_MCUs.pretty"))
        self.assertEqual(outputs["shapes"], Path("/tmp/Shared_MCUs.3dshapes"))

    def test_run_easyeda2kicad_passes_full_overwrite(self):
        with mock.patch("import_lcsc.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            import_lcsc.run_easyeda2kicad(
                "C123", Path("/tmp/Shared_MCUs"), "/usr/bin/easyeda2kicad"
            )
        command = run.call_args.args[0]
        self.assertIn("--full", command)
        self.assertIn("--overwrite", command)
        self.assertIn("C123", command)
        self.assertIn("/tmp/Shared_MCUs", command)

    def test_run_easyeda2kicad_reports_failure(self):
        with mock.patch("import_lcsc.subprocess.run") as run:
            run.return_value = mock.Mock(
                returncode=1, stdout="", stderr="bad part"
            )
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "bad part"):
                import_lcsc.run_easyeda2kicad("C123", Path("/tmp/x"))

    def test_read_single_footprint_rejects_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "A.kicad_mod").write_text("a")
            (directory / "B.kicad_mod").write_text("b")
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "exactly one"):
                import_lcsc.read_single_footprint(directory)

    def test_read_single_step_ignores_wrl(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "PKG.step").write_text("step")
            (directory / "PKG.wrl").write_text("wrl")
            self.assertEqual(
                import_lcsc.read_single_step(directory), directory / "PKG.step"
            )

    def test_transform_rewrites_name_and_model_path(self):
        text = (
            '(footprint "OLD" (layer F.Cu)\n'
            '  (model "/tmp/Shared_MCUs.3dshapes/OLD.wrl"\n'
            "    (offset (xyz 1 2 3))\n"
            "    (scale (xyz 1 1 1))\n"
            "    (rotate (xyz 0 0 90))\n"
            "  )\n"
            ")\n"
        )
        updated = import_lcsc.transform_footprint(text, "OLD", "PKG", "PKG")
        self.assertIn('(footprint "PKG"', updated)
        self.assertIn('"${KICAD_LIBS}/3dmodels/PKG.step"', updated)
        self.assertIn("(offset (xyz 1 2 3))", updated)
        self.assertIn("(rotate (xyz 0 0 90))", updated)
        self.assertNotIn(".wrl", updated)

    def test_transform_requires_a_model_block(self):
        with self.assertRaisesRegex(import_lcsc.LcscImportError, "no model"):
            import_lcsc.transform_footprint(
                '(footprint "OLD"\n)\n', "OLD", "PKG", "PKG"
            )


class YamlEntryTests(unittest.TestCase):
    def make_entry(self, **overrides):
        values = {
            "mpn": "TMC9660ATB+",
            "lcsc": {
                "manufacturer": "Analog Devices",
                "package": "TQFN-64",
                "category": "Motor Drivers",
                "description": "70V smart gate driver",
                "datasheet": "https://example.test/tmc.pdf",
                "name": "TMC9660",
            },
            "symbol_lib": "Shared_MCUs",
            "symbol_name": "TMC9660ATB+",
            "footprint_lib": "Shared",
            "footprint_name": "TQFN-64",
            "package": "TQFN-64",
            "symbol_props": {"Keywords": "TMC9660 gate driver"},
            "part_id": "TMC9660ATB+",
            "height_mm": 0.8,
        }
        values.update(overrides)
        return import_lcsc.build_part_entry(**values)

    def test_entry_contains_required_fields(self):
        entry = self.make_entry()
        self.assertEqual(entry["id"], "TMC9660ATB+")
        self.assertEqual(entry["symbol"], "Shared_MCUs:TMC9660ATB+")
        self.assertEqual(entry["value"], "TMC9660ATB+")
        self.assertEqual(
            entry["description"], "70V smart gate driver"
        )
        self.assertEqual(
            entry["datasheet"], "https://example.test/tmc.pdf"
        )
        self.assertIn("TMC9660ATB+", entry["keywords"])
        footprint = entry["footprints"][0]
        self.assertEqual(footprint["footprint"], "Shared:TQFN-64")
        self.assertEqual(footprint["package"], "TQFN-64")
        self.assertEqual(footprint["mpns"][0]["mpn"], "TMC9660ATB+")
        self.assertEqual(
            footprint["mpns"][0]["manufacturer"], "Analog Devices"
        )
        self.assertEqual(footprint["mpns"][0]["height_mm"], 0.8)

    def test_height_omitted_when_not_given(self):
        entry = self.make_entry(height_mm=None)
        self.assertNotIn("height_mm", entry["footprints"][0]["mpns"][0])

    def test_append_preserves_existing_text_and_parses(self):
        existing = (
            "version: 1\nparts:\n"
            "  - id: OLD\n"
            "    symbol: Shared_MCUs:OLD\n"
            "    footprints:\n"
            "      - mpns:\n"
            "          - mpn: OLD-MPN\n"
        )
        entry = self.make_entry(part_id="2,5 MSF 02")
        updated = import_lcsc.append_part_yaml_text(existing, entry)
        self.assertTrue(updated.startswith(existing))
        doc = yaml.safe_load(updated)
        self.assertEqual(len(doc["parts"]), 2)
        self.assertEqual(doc["parts"][1]["id"], "2,5 MSF 02")
        self.assertEqual(doc["parts"][1]["footprints"][0]["mpns"][0]["mpn"],
                         "TMC9660ATB+")

    def test_collision_detects_id_and_mpn(self):
        doc = {
            "version": 1,
            "parts": [
                {
                    "id": "OLD",
                    "footprints": [
                        {"mpns": [{"mpn": "OLD-MPN"}]}
                    ],
                }
            ],
        }
        self.assertTrue(import_lcsc.yaml_collisions(doc, "OLD", "NEW-MPN"))
        self.assertTrue(import_lcsc.yaml_collisions(doc, "NEW", "OLD-MPN"))
        self.assertEqual(
            import_lcsc.yaml_collisions(doc, "NEW", "NEW-MPN"), []
        )


class LibraryTests(unittest.TestCase):
    def test_library_uri(self):
        self.assertEqual(
            import_lcsc.library_uri("symbol", "Shared_MCUs"),
            "${KICAD_LIBS}/symbols/Shared_MCUs.kicad_sym",
        )
        self.assertEqual(
            import_lcsc.library_uri("footprint", "Shared"),
            "${KICAD_LIBS}/footprints/Shared.pretty",
        )

    def test_append_row_preserves_existing(self):
        table = (
            "(sym_lib_table\n\t(version 7)\n"
            '\t(lib (name "Old") (type "KiCad") '
            '(uri "${KICAD_LIBS}/symbols/Old.kicad_sym") '
            '(options "") (descr "old"))\n)\n'
        )
        updated = import_lcsc.append_library_row(
            table,
            "Shared_MCUs",
            "${KICAD_LIBS}/symbols/Shared_MCUs.kicad_sym",
            "Shared MCU symbols",
        )
        self.assertIn('(name "Old")', updated)
        self.assertIn('(name "Shared_MCUs")', updated)
        self.assertTrue(updated.rstrip().endswith(")"))

    def test_append_row_rejects_nickname_with_different_uri(self):
        table = (
            "(sym_lib_table\n\t(version 7)\n"
            '\t(lib (name "Shared_MCUs") (type "KiCad") '
            '(uri "${KICAD_LIBS}/old.kicad_sym") '
            '(options "") (descr "old"))\n)\n'
        )
        with self.assertRaisesRegex(import_lcsc.LcscImportError, "different URI"):
            import_lcsc.append_library_row(
                table,
                "Shared_MCUs",
                "${KICAD_LIBS}/symbols/Shared_MCUs.kicad_sym",
                "new",
            )

    def test_check_collisions_blocks_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "symbols").mkdir()
            (root / "footprints" / "Shared.pretty").mkdir(parents=True)
            (root / "3dmodels").mkdir()
            (root / "symbols" / "Shared_MCUs.kicad_sym").write_text(
                '(kicad_symbol_lib\n\t(symbol "PART"\n\t)\n)\n'
            )
            (root / "footprints" / "Shared.pretty" / "PKG.kicad_mod").write_text(
                '(footprint "PKG"\n)\n'
            )
            (root / "3dmodels" / "PKG.step").write_text("STEP")
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "already exists"):
                import_lcsc.check_collisions(
                    root=root,
                    symbol_lib="Shared_MCUs",
                    symbol_name="PART",
                    footprint_lib="Shared",
                    footprint_name="PKG",
                    model_name="PKG",
                    doc={"version": 1, "parts": []},
                    part_id="PART",
                    mpn="PART",
                    replace=False,
                )

    def test_check_collisions_allows_files_with_replace_but_not_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "symbols").mkdir()
            (root / "footprints" / "Shared.pretty").mkdir(parents=True)
            (root / "3dmodels").mkdir()
            (root / "symbols" / "Shared_MCUs.kicad_sym").write_text(
                '(kicad_symbol_lib\n\t(symbol "PART"\n\t)\n)\n'
            )
            (root / "footprints" / "Shared.pretty" / "PKG.kicad_mod").write_text(
                '(footprint "PKG"\n)\n'
            )
            (root / "3dmodels" / "PKG.step").write_text("STEP")
            import_lcsc.check_collisions(
                root=root,
                symbol_lib="Shared_MCUs",
                symbol_name="PART",
                footprint_lib="Shared",
                footprint_name="PKG",
                model_name="PKG",
                doc={"version": 1, "parts": []},
                part_id="PART",
                mpn="PART",
                replace=True,
            )
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "part id"):
                import_lcsc.check_collisions(
                    root=root,
                    symbol_lib="Shared_MCUs",
                    symbol_name="NEW",
                    footprint_lib="Shared",
                    footprint_name="NEW",
                    model_name="NEW",
                    doc={"version": 1, "parts": [{"id": "PART"}]},
                    part_id="PART",
                    mpn="PART",
                    replace=True,
                )


class TransactionTests(unittest.TestCase):
    def make_repo(self, root, build_exit=0, build_stderr=""):
        (root / "parts.yaml").write_text("version: 1\nparts: []\n")
        (root / "build_parts_db.py").write_text(
            "import sys\n"
            "print('built shadow/parts.sqlite (0 MPN rows)')\n"
            f"sys.stderr.write({build_stderr!r})\n"
            f"sys.exit({build_exit})\n"
        )
        (root / "tests").mkdir()
        (root / "tests" / "__init__.py").write_text("")
        (root / "tests" / "test_ok.py").write_text(
            "import unittest\n"
            "\n"
            "\n"
            "class OkTests(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n"
        )

    def symbol_change(self, root):
        return import_lcsc.PlannedChange(
            root / "symbols" / "Shared.kicad_sym",
            b'(kicad_symbol_lib\n\t(symbol "NEW"\n\t)\n)\n',
        )

    def test_validate_in_shadow_fails_when_kicad_cli_upgrade_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.make_repo(root)
            change = self.symbol_change(root)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if command[0] == "/usr/bin/kicad-cli":
                    return mock.Mock(
                        returncode=1,
                        stdout="upgrade stdout",
                        stderr="bad s-expression",
                    )
                return mock.Mock(
                    returncode=0,
                    stdout="built build/parts.sqlite (1 MPN rows)\n",
                    stderr="",
                )

            with mock.patch(
                "import_lcsc.shutil.which", return_value="/usr/bin/kicad-cli"
            ) as which, mock.patch(
                "import_lcsc.subprocess.run", side_effect=fake_run
            ):
                with self.assertRaises(
                    import_lcsc.LcscImportError
                ) as raised:
                    import_lcsc.validate_in_shadow(root, [change])
            message = str(raised.exception)
            self.assertTrue(message.startswith("validation failed:"))
            self.assertIn("kicad-cli", message)
            self.assertIn("upgrade stdout", message)
            self.assertIn("bad s-expression", message)
            self.assertEqual(
                len(
                    [
                        command
                        for command in commands
                        if command[0] == "/usr/bin/kicad-cli"
                    ]
                ),
                1,
            )
            which.assert_called_once_with("kicad-cli")

    def test_validate_in_shadow_upgrades_symbol_changes_with_kicad_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.make_repo(root)
            change = self.symbol_change(root)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if command[0] == "/usr/bin/kicad-cli":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(
                    returncode=0,
                    stdout="built build/parts.sqlite (1 MPN rows)\n",
                    stderr="",
                )

            with mock.patch(
                "import_lcsc.shutil.which", return_value="/usr/bin/kicad-cli"
            ), mock.patch(
                "import_lcsc.subprocess.run", side_effect=fake_run
            ):
                import_lcsc.validate_in_shadow(root, [change])

            kicad_commands = [
                command
                for command in commands
                if command[0] == "/usr/bin/kicad-cli"
            ]
            self.assertEqual(len(kicad_commands), 1)
            command = kicad_commands[0]
            self.assertEqual(command[1:4], ["sym", "upgrade", "--force"])
            self.assertEqual(command[4], "--output")
            self.assertNotEqual(command[5], command[6])
            self.assertTrue(command[6].endswith("symbols/Shared.kicad_sym"))

    def test_validate_in_shadow_skips_kicad_cli_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.make_repo(root)
            change = self.symbol_change(root)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                return mock.Mock(
                    returncode=0,
                    stdout="built build/parts.sqlite (1 MPN rows)\n",
                    stderr="",
                )

            with mock.patch(
                "import_lcsc.shutil.which", return_value=None
            ), mock.patch(
                "import_lcsc.subprocess.run", side_effect=fake_run
            ):
                import_lcsc.validate_in_shadow(root, [change])

            self.assertEqual(len(commands), 2)
            self.assertNotIn(
                "/usr/bin/kicad-cli",
                [command[0] for command in commands],
            )

    def test_validate_in_shadow_does_not_mutate_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.make_repo(root)
            change = import_lcsc.PlannedChange(root / "new.txt", b"hello")
            import_lcsc.validate_in_shadow(root, [change])
            self.assertFalse((root / "new.txt").exists())

    def test_validate_in_shadow_raises_on_build_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.make_repo(root, build_exit=3)
            change = import_lcsc.PlannedChange(root / "new.txt", b"hello")
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "validation failed"):
                import_lcsc.validate_in_shadow(root, [change])
            self.assertFalse((root / "new.txt").exists())

    def test_validate_in_shadow_rejects_build_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.make_repo(root, build_stderr="warning: value mismatch\n")
            change = import_lcsc.PlannedChange(root / "new.txt", b"hello")
            with self.assertRaisesRegex(import_lcsc.LcscImportError, "validation failed"):
                import_lcsc.validate_in_shadow(root, [change])
            self.assertFalse((root / "new.txt").exists())

    def test_apply_changes_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "a.txt"
            original.write_text("old")
            changes = [
                import_lcsc.PlannedChange(original, b"new"),
                import_lcsc.PlannedChange(root / "b.txt", b"new-b"),
            ]
            with mock.patch(
                "import_lcsc.os.replace", side_effect=[None, OSError("boom")]
            ):
                with self.assertRaises(OSError):
                    import_lcsc.apply_changes(changes)
            self.assertEqual(original.read_text(), "old")
            self.assertFalse((root / "b.txt").exists())

    def test_apply_changes_commits_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a.txt"
            path.write_text("old")
            import_lcsc.apply_changes(
                [import_lcsc.PlannedChange(path, b"new")]
            )
            self.assertEqual(path.read_text(), "new")


NEW_SYMBOL_LIB = """(kicad_symbol_lib
  (version 20211014)
  (symbol "NEWPART"
    (property "Reference" "U")
    (property "Value" "NEWPART")
    (property "Footprint" "Shared_MCUs:PKG")
    (property "Datasheet" "https://example.test/new.pdf")
    (property "ki_keywords" "Test part")
    (symbol "NEWPART_0_1"
      (rectangle (start 0 0) (end 1 1)
        (stroke (width 0) (type default))
        (fill (type none)))
    )
  )
)
"""

NEW_FOOTPRINT = """(footprint "PKG" (layer F.Cu)
  (property "LCSC Part" "C123")
  (model "/tmp/Shared_MCUs.3dshapes/PKG.wrl"
    (offset (xyz 0 0 0))
    (scale (xyz 1 1 1))
    (rotate (xyz 0 0 0))
  )
)
"""


CANDIDATE = {
    "lcsc": "C123", "mpn": "NEWPART", "manufacturer": "Acme",
    "package": "PKG", "stock": 1, "type": "Extended",
    "category": "Test", "description": "New part", "name": "NEWPART",
    "datasheet": "https://example.test/new.pdf", "url": "",
}

EXISTING_PARTS = (
    "version: 1\n"
    "parts:\n"
    "  - id: EXISTING\n"
    "    symbol: Shared_Resistors:R_0603\n"
    "    value: R_0603\n"
    "    description: Resistor 0603\n"
    "    datasheet: https://example.test/r0603.pdf\n"
    "    keywords: R resistor 0603\n"
    "    footprints:\n"
    "      - footprint: Shared:R_0603\n"
    "        package: 0603\n"
    "        mpns:\n"
    "          - mpn: EXISTING-MPN\n"
)

EXISTING_SYMBOL_LIB = """(kicad_symbol_lib
  (version 20211014)
  (symbol "NEWPART"
    (property "Reference" "U")
    (property "Value" "OLD")
    (property "Footprint" "Shared:OLD")
    (property "Datasheet" "https://example.test/old.pdf")
    (property "Description" "OLD DESCRIPTION")
    (property "Keywords" "old")
    (symbol "NEWPART_0_1"
      (rectangle (start 0 0) (end 2 2)
        (stroke (width 0) (type default))
        (fill (type none)))
    )
  )
  (symbol "OTHER"
    (property "Reference" "U")
    (property "Value" "OTHER")
    (property "Footprint" "Shared:OTHER")
    (property "Datasheet" "https://example.test/other.pdf")
    (property "Description" "Other part")
    (property "Keywords" "other")
  )
)
"""


class CliTests(unittest.TestCase):
    def test_every_argument_has_help_text(self):
        parser = import_lcsc.build_parser()
        undocumented = [
            action.dest
            for action in parser._actions
            if action.dest != "help" and not action.help
        ]
        self.assertEqual(undocumented, [])

    def test_help_displays_flag_defaults(self):
        help_text = " ".join(
            import_lcsc.build_parser().format_help().split()
        )
        self.assertEqual(help_text.count("default: generated name"), 2)
        self.assertIn("default: the MPN", help_text)
        self.assertIn("default: current directory", help_text)
        self.assertIn("default: easyeda2kicad", help_text)

    def _make_repo(self, tmp, *, existing_symbol=False):
        root = Path(tmp) / "repo"
        root.mkdir()
        make_fixture_repo(root)
        shutil.copy(Path("build_parts_db.py"), root / "build_parts_db.py")
        (root / "tests").mkdir()
        (root / "tests" / "__init__.py").write_text("")
        (root / "tests" / "test_smoke.py").write_text(
            "def test_ok():\n    assert True\n"
        )
        (root / "parts.yaml").write_text(EXISTING_PARTS)
        if existing_symbol:
            (root / "symbols" / "Shared_MCUs.kicad_sym").write_text(
                EXISTING_SYMBOL_LIB
            )
        return root

    def _fake_run(self, lcsc_id, stem, executable="easyeda2kicad"):
        stem.with_suffix(".kicad_sym").write_text(NEW_SYMBOL_LIB)
        pretty = Path(str(stem) + ".pretty")
        pretty.mkdir(parents=True)
        (pretty / "PKG.kicad_mod").write_text(NEW_FOOTPRINT)
        shapes = Path(str(stem) + ".3dshapes")
        shapes.mkdir(parents=True)
        (shapes / "PKG.step").write_text("STEP")
        (shapes / "PKG.wrl").write_text("WRL")

    def _invoke(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            code = import_lcsc.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def _run_import(self, root, *extra, candidates=None):
        candidates = [CANDIDATE] if candidates is None else candidates
        with mock.patch(
            "import_lcsc.search_jlcpcb", return_value=candidates
        ), mock.patch(
            "import_lcsc.run_easyeda2kicad", side_effect=self._fake_run
        ) as run, mock.patch(
            "import_lcsc.validate_in_shadow"
        ) as validate:
            code, out, err = self._invoke(
                [
                    "NEWPART",
                    "--symbol-lib", "Shared_MCUs",
                    "--footprint-lib", "Shared",
                    *extra,
                    "--root", str(root),
                ]
            )
        return code, out, err, run, validate

    def test_search_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "parts.yaml").write_text("version: 1\nparts: []\n")
            with mock.patch(
                "import_lcsc.search_jlcpcb", return_value=[CANDIDATE]
            ):
                code, out, err = self._invoke(
                    [
                        "NEWPART",
                        "--search",
                        "--symbol-lib", "Shared_MCUs",
                        "--footprint-lib", "Shared",
                        "--root", str(root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("C123", out)
            self.assertEqual(err, "")
            self.assertEqual(
                (root / "parts.yaml").read_text(), "version: 1\nparts: []\n"
            )

    def test_search_does_not_require_library_flags_or_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "import_lcsc.search_jlcpcb", return_value=[CANDIDATE]
            ):
                code, out, err = self._invoke(
                    ["NEWPART", "--search", "--root", tmp]
                )
            self.assertEqual(code, 0)
            self.assertIn("C123", out)
            self.assertEqual(err, "")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_import_without_search_still_requires_library_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("import_lcsc.search_jlcpcb") as search:
                code, out, err = self._invoke(
                    ["NEWPART", "--root", tmp]
                )
            search.assert_not_called()
        self.assertEqual(code, 1)
        self.assertIn("--symbol-lib", err)
        self.assertIn("--footprint-lib", err)

    def test_full_import_writes_valid_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp)
            code, out, err, run, validate = self._run_import(
                root, "--height-mm", "1.0"
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            run.assert_called_once()
            validate.assert_called_once()

            symbol_text = (
                root / "symbols" / "Shared_MCUs.kicad_sym"
            ).read_text()
            self.assertIn('(symbol "NEWPART"', symbol_text)
            self.assertIn('"Shared:PKG"', symbol_text)
            footprint_text = (
                root / "footprints" / "Shared.pretty" / "PKG.kicad_mod"
            ).read_text()
            self.assertIn('"${KICAD_LIBS}/3dmodels/PKG.step"', footprint_text)
            self.assertNotIn(".wrl", footprint_text)
            self.assertTrue((root / "3dmodels" / "PKG.step").exists())
            self.assertFalse((root / "3dmodels" / "PKG.wrl").exists())
            doc = yaml.safe_load((root / "parts.yaml").read_text())
            self.assertEqual(len(doc["parts"]), 2)
            self.assertEqual(doc["parts"][1]["id"], "NEWPART")
            self.assertEqual(
                doc["parts"][1]["footprints"][0]["mpns"][0]["height_mm"],
                1.0,
            )
            self.assertIn(
                'Shared_MCUs',
                (root / "sym-lib-table").read_text(),
            )
            self.assertIn(
                'Shared',
                (root / "fp-lib-table").read_text(),
            )
            from build_parts_db import build

            rows = build(
                root / "parts.yaml",
                root / "build" / "parts.sqlite",
                root,
                "test",
            )
            self.assertEqual(len(rows), 2)

    def test_replace_replaces_existing_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp, existing_symbol=True)
            symbol_path = root / "symbols" / "Shared_MCUs.kicad_sym"
            footprint_path = (
                root / "footprints" / "Shared.pretty" / "PKG.kicad_mod"
            )
            footprint_path.write_text('(footprint "PKG"\n)\n')
            model_path = root / "3dmodels" / "PKG.step"
            model_path.write_text("OLD STEP")
            before = import_lcsc.top_level_symbol_blocks(
                symbol_path.read_text()
            )
            code, out, err, run, validate = self._run_import(
                root, "--height-mm", "1.0", "--replace"
            )
            self.assertEqual(code, 0)
            self.assertEqual(
                err,
                f"replacing {symbol_path}\n"
                f"replacing {footprint_path}\n"
                f"replacing {model_path}\n",
            )
            run.assert_called_once()
            validate.assert_called_once()

            symbol_text = symbol_path.read_text()
            after = import_lcsc.top_level_symbol_blocks(symbol_text)
            self.assertEqual(set(after), {"NEWPART", "OTHER"})
            self.assertIn(
                '\n  (symbol "NEWPART"\n    (property "Reference" "U")',
                symbol_text,
            )
            self.assertNotIn("OLD DESCRIPTION", symbol_text)
            self.assertNotIn('"Shared:OLD"', symbol_text)
            self.assertIn('"Shared:PKG"', symbol_text)
            self.assertEqual(after["OTHER"], before["OTHER"])
            self.assertIn(
                '"${KICAD_LIBS}/3dmodels/PKG.step"',
                footprint_path.read_text(),
            )
            self.assertEqual(model_path.read_text(), "STEP")

    def test_duplicate_symbol_without_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp, existing_symbol=True)
            symbol_path = root / "symbols" / "Shared_MCUs.kicad_sym"
            before = symbol_path.read_text()
            code, out, err, _, validate = self._run_import(root)
            self.assertNotEqual(code, 0)
            self.assertIn("already exists", err)
            validate.assert_not_called()
            self.assertEqual(symbol_path.read_text(), before)
            self.assertFalse(
                (root / "footprints" / "Shared.pretty" / "PKG.kicad_mod")
                .exists()
            )
            self.assertFalse((root / "3dmodels" / "PKG.step").exists())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp)
            parts_before = (root / "parts.yaml").read_text()
            code, out, err, run, validate = self._run_import(
                root, "--dry-run", "--height-mm", "1.0"
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            run.assert_called_once()
            validate.assert_not_called()
            self.assertIn("write ", out)
            self.assertEqual(
                (root / "parts.yaml").read_text(), parts_before
            )
            self.assertFalse(
                (root / "symbols" / "Shared_MCUs.kicad_sym").exists()
            )
            self.assertFalse(
                (root / "footprints" / "Shared.pretty" / "PKG.kicad_mod")
                .exists()
            )
            self.assertFalse((root / "3dmodels" / "PKG.step").exists())
            self.assertFalse((root / "sym-lib-table").exists())
            self.assertFalse((root / "fp-lib-table").exists())

    def test_lcsc_id_bypasses_exact_mpn_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp)
            code, out, err, run, validate = self._run_import(
                root,
                "--lcsc-id", "C123",
                "--height-mm", "1.0",
                candidates=[{**CANDIDATE, "mpn": "OTHER"}],
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], "C123")
            validate.assert_called_once()
            doc = yaml.safe_load((root / "parts.yaml").read_text())
            self.assertEqual(doc["parts"][1]["id"], "NEWPART")

    def test_height_reminder_when_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp)
            code, out, err, run, validate = self._run_import(root)
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertIn(
                "note: height_mm was not recorded", out
            )
            doc = yaml.safe_load((root / "parts.yaml").read_text())
            self.assertNotIn(
                "height_mm", doc["parts"][1]["footprints"][0]["mpns"][0]
            )

    def test_no_exact_match_fails_and_prints_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_repo(tmp)
            parts_before = (root / "parts.yaml").read_text()
            code, out, err, run, validate = self._run_import(
                root, candidates=[{**CANDIDATE, "mpn": "OTHER"}]
            )
            self.assertNotEqual(code, 0)
            self.assertIn("C123", out)
            self.assertIn("no exact MPN match", err)
            run.assert_not_called()
            validate.assert_not_called()
            self.assertEqual(
                (root / "parts.yaml").read_text(), parts_before
            )
            self.assertFalse(
                (root / "symbols" / "Shared_MCUs.kicad_sym").exists()
            )
