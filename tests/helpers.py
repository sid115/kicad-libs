from pathlib import Path


def write_symbol(root: Path, nickname: str, name: str, properties: dict) -> None:
    (root / "symbols").mkdir(parents=True, exist_ok=True)
    body = "".join(
        f'\t\t(property "{key}" "{value}")\n' for key, value in properties.items()
    )
    (root / "symbols" / f"{nickname}.kicad_sym").write_text(
        f'(kicad_symbol_lib\n\t(symbol "{name}"\n{body}\t)\n)\n',
        encoding="utf-8",
    )


def write_symbol_library(directory: Path, nickname: str, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{nickname}.kicad_sym").write_text(
        f'(kicad_symbol_lib\n\t(symbol "{name}"\n\t)\n)\n',
        encoding="utf-8",
    )


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
