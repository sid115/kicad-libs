# AGENTS.md

Operating guide for AI agents working in this repository.

`README.md` and `CONTRIBUTING.md` are the authoritative human documentation.
This file summarizes them and adds the procedure for importing a part from a
user's active KiCad project. If this file and those documents disagree, follow
`README.md` / `CONTRIBUTING.md` and tell the user about the conflict.

## What this repository is

A shared KiCad 10 symbol, footprint and 3D-model library. `parts.yaml` defines
every part. `build_parts_db.py` validates the YAML against the library
files and generates `build/parts.sqlite`, which KiCad reads through
`parts.kicad_dbl` and the shared `sym-lib-table` / `fp-lib-table`.

Key paths:

| Path | Purpose |
| --- | --- |
| `parts.yaml` | Part catalogue: symbols, footprints, MPNs. |
| `symbols/<Nick>.kicad_sym` | KiCad symbol library. Nickname must match filename stem. |
| `footprints/<Nick>.pretty/<Name>.kicad_mod` | KiCad footprint library. Nickname must match directory stem. |
| `3dmodels/<Name>.step` or `.stp` | 3D models referenced from footprints. |
| `build_parts_db.py` | Validates `parts.yaml` and writes `build/parts.sqlite`. |
| `parts.kicad_dbl` | KiCad database-library definition. Do not edit unless the schema changes. |
| `sym-lib-table`, `fp-lib-table` | Shared library registration. |
| `tests/test_build_parts_db.py` | Unit and repository-integrity tests. |
| `build/` | Generated. Never commit it. |

Every user must have the `KICAD_LIBS` path variable pointing at their clone;
the 3D-model paths rely on it. Do not change a user's KiCad preferences.

## Core model

A part is one symbol (LIB_ID `<Nick>:<Name>`), one or more footprints, one
3D model per footprint, and at least one MPN per footprint. The builder emits
one database row per MPN.

LIB_ID rules:

- Symbol: `<Nick>` must equal the `.kicad_sym` filename stem; `<Name>` must be
  a top-level `(symbol "<Name>" ...)` in that file. Nested units such as
  `<Name>_0_1` are symbol units, not separate parts.
- Footprint: `<Nick>` must equal the `.pretty` directory stem; `<Name>` must
  equal the `.kicad_mod` filename stem and its `(footprint "<Name>" ...)` name.
- A footprint LIB_ID must resolve to
  `footprints/<Nick>.pretty/<Name>.kicad_mod`.
- Reference 3D models as `${KICAD_LIBS}/3dmodels/<file>.step` (or `.stp`).
  `${KICAD_LIBS}` is the only supported variable.
- A part with `external: true` may use a stock KiCad symbol such as `Device:R`
  without adding it under `symbols/`. External parts must set `value`,
  `description`, `datasheet` and `keywords` explicitly in `parts.yaml`, and
  every footprint entry must set `footprint`; symbol-Footprint fallback is not
  available. Footprints and 3D models remain repository-local.

## Primary task: import a new part from the user's project

The user is usually designing the part inside another KiCad project and wants
to move it into this shared library. Copy the files from their project. Never
modify the project; only read it.

### 1. Establish the facts

Ask for, or derive from the project, all of the following before editing:

- Path to the active KiCad project (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`).
- Symbol source: the project-local `.kicad_sym` (or external library) and the
  exact symbol name.
- Footprint source(s): the `.pretty` directory / `.kicad_mod` file(s), and the
  package each corresponds to.
- 3D model file(s): `.step`, `.stp` or `.wrl`, and their locations.
- MPN(s), manufacturer(s), datasheet URL, description and keywords.
- Package height in millimetres, when the datasheet or 3D model provides it.
- Target library nickname. Default to the existing `Shared_MCUs` for symbols and
  `Shared` for footprints, unless the user wants a new family library.
- Desired part `id` (default: the MPN or symbol name) and `value`.

Never invent an MPN, manufacturer, datasheet or package. If a required fact is
missing, ask. Do not silently choose between multiple candidate files.

### 2. Inspect the source project read-only

Locate the project root via the `.kicad_pro` file. Use the project's
`sym-lib-table` / `fp-lib-table`, schematic and board files only to resolve
which library files and footprints the project uses. Confirm the symbol name,
footprint names and model filenames before copying.

### 3. Add the symbol

- Append only the requested top-level symbol (including its nested units,
  properties and graphics) to `symbols/<Nick>.kicad_sym`. Create the file if
  the library nickname is new.
- Keep the S-expression structure valid and parentheses balanced. Preserve the
  original symbol content; do not re-draw or "clean up" the user's work.
- If the symbol name already exists in the target library, stop and ask
  whether to replace it or use a different name. Never overwrite silently.
- Update the symbol's `Footprint` property to the final target LIB_ID
  (`<FootprintNick>:<Name>`) if the source used a project-local nickname.
- Make sure `Value`, `Description`, `Datasheet` and `Keywords` are present, and
  copy them from the symbol into the `parts.yaml` entry (see below).

### 4. Add the footprint(s)

- Copy each `.kicad_mod` into `footprints/<Nick>.pretty/`. Create the
  directory for a new nickname. Keep the filename stem and the
  `(footprint "<Name>" ...)` name consistent.
- If the footprint is renamed, update the name inside the file and in
  `parts.yaml`.
- Keep pad geometry, silkscreen and courtyard exactly as supplied unless the
  user asks for a change.
- Rewrite model paths (next step). Preserve the rest of each `(model ...)`
  block, including `offset`, `scale` and `rotate`.

### 5. Add the 3D model

- Copy the model into `3dmodels/`, using a stable name derived from the part
  or MPN. Prefer `.step` / `.stp`, matching `README.md`.
- If the source is only `.wrl`, ask the user whether to add/convert a STEP
  model rather than importing it silently.
- Rewrite every `(model "...")` path in the copied footprint to
  `${KICAD_LIBS}/3dmodels/<file>`. Replace `${KIPRJMOD}`, absolute paths or
  any other variable; the builder rejects other `${...}` variables and
  requires the target file to exist.

### 6. Add the part to `parts.yaml`

Append an entry like:

```yaml
version: 1
parts:
  - id: STM32G474RET6
    symbol: Shared_MCUs:STM32G474RET6
    value: STM32G474RET6
    description: ARM Cortex-M4 MCU, 512KB flash, 128KB SRAM, 170MHz, LQFP-64
    datasheet: https://www.st.com/resource/en/datasheet/stm32g474cb.pdf
    keywords: STM32G474RET6 STM32G4 Cortex-M4 MCU LQFP-64
    footprints:
      - footprint: Shared:QFP50P1200X1200X160-64N
        package: LQFP-64
        mpns:
          - mpn: STM32G474RET6
            manufacturer: STMicroelectronics
            height_mm: 1.6
```

Rules:

- `version` must be `1`; `parts` must be a non-empty list.
- `id` and every MPN must be globally unique.
- `symbol` is required and must be a valid LIB_ID.
- `parts.yaml` must always be filled in for every part. Do not rely on the
  symbol's fields as a fallback: copy `Value`, `Description`, `Datasheet` and
  `Keywords` from the symbol into the YAML entry, and set every footprint's
  `footprint` and `package`.
- `footprints` must be non-empty; every footprint needs at least one MPN.
- `footprint` may be omitted for a single-footprint part; it then falls back
  to the symbol's `Footprint` property, but explicitly listing it is preferred.
  For `external: true` parts, `footprint` is mandatory on every footprint entry.
- `external` must be a boolean when present. For external parts, `value`,
  `description`, `datasheet` and `keywords` must be set explicitly in `parts.yaml`;
  `value` and `description` must not be empty.
- `package` is free text describing the physical package (e.g. `LQFP-64`).
- Record the package height as `height_mm` on each MPN when it is known from
  the datasheet or 3D model. It is optional but encouraged. It must be a
  positive number in millimetres and is resolved per MPN, not per footprint or
  symbol. Do not use a symbol `Height` property as the source; the builder
  does not read it.
- Repeat `mpns` for pin-compatible variants; use one footprint entry per
  distinct package. MPNs belong to the footprint with their exact package.
- Per-MPN `description` and `datasheet` are intentional overrides and do not
  warn.

### 7. Register new libraries (only when needed)

If and only if a new nickname was introduced, add matching rows to both
tables. Symbol table:

```lisp
(lib (name "<Nick>") (type "KiCad") (uri "${KICAD_LIBS}/symbols/<Nick>.kicad_sym") (options "") (descr "..."))
```

Footprint table:

```lisp
(lib (name "<Nick>") (type "KiCad") (uri "${KICAD_LIBS}/footprints/<Nick>.pretty") (options "") (descr "..."))
```

Do not re-register existing `Shared_MCUs`, `Shared` or `SharedDB` entries.

### 8. Validate before reporting success

Run from the repository root:

```sh
python3 build_parts_db.py
python3 -m unittest discover -s tests -t . -v
```

The builder must print `built build/parts.sqlite (N MPN rows)`. Resolve every
`warning:` it prints; warnings mean YAML and symbol disagree. When Nix is
available, run the full KiCad validation used by CI:

```sh
nix flake check --keep-going --option max-jobs 1
```

This upgrades and renders every symbol and footprint with `kicad-cli`. If Nix
is unavailable, say so rather than claiming full validation.

Also run `git status` and confirm `build/` is not staged.

### 9. Git workflow

Only create branches, commits or pull requests when the user asks. When they
do, follow `CONTRIBUTING.md`:

- `master` is protected. Never push to it.
- Branch from `master`, named after the part (e.g. `stm32g474ret6`).
- Keep one part or one logical change per branch and pull request.
- Do not commit `build/`, `__pycache__/`, `.direnv/`, backups or editor files.
- Do not commit files copied from the user's project that do not belong in
  this library (schematics, boards, project files, secrets).

## Field precedence

| Database field | Resolution order |
| --- | --- |
| `value` | YAML part `value`, then symbol `Value` |
| `description` | MPN `description`, then YAML part `description`, then symbol `Description` |
| `datasheet` | MPN `datasheet`, then YAML part `datasheet`, then symbol `Datasheet` |
| `keywords` | YAML part `keywords`, then symbol `Keywords` |
| `footprint` | YAML footprint entry, then symbol `Footprint` |
| `height_mm` | MPN `height_mm` only; no symbol or footprint fallback |

YAML wins when both are set and differ, and the builder warns. A single
footprint that differs from the symbol's `Footprint` also warns. The builder
treats multiple footprints as a deliberate mapping and does not warn.

The fallbacks exist for older entries and diagnostics only. New and updated
parts must explicitly set these fields in `parts.yaml`, copied from the symbol.
External parts have no symbol property source, so all four metadata fields and
every footprint must be explicit. The builder checks external symbols against
KiCad symbol libraries when `KICAD*_SYMBOL_DIR`, a standard install path, or
`--kicad-symbols-dir` provides them; otherwise the existence check is skipped.

## Constraints

- Read `README.md` and `CONTRIBUTING.md` before changing the repo.
- Never edit `build/parts.sqlite`, `build/` or `result*` by hand.
- Never edit `parts.kicad_dbl` unless the database schema changes.
- Never use absolute paths or `${KIPRJMOD}` in footprints; use
  `${KICAD_LIBS}`.
- Never invent metadata or silently resolve conflicting files.
- Never overwrite an existing symbol, footprint or part entry without asking.
- Always populate `parts.yaml` from the symbol; never leave fields to fallback.
- Use `external: true` only for stock KiCad symbols. Never use it to bypass a
  missing or broken local symbol.
- Do not derive `height_mm` from a symbol `Height` property; use the datasheet
  or 3D model and record it on the MPN.
- Do not weaken tests or validation to make a build pass.
- Keep the user's source project untouched.

## Troubleshooting

- `symbol '<Nick>:<Name>' not found under symbols/`. The nickname must match
  the `.kicad_sym` stem and the name must be a top-level symbol. If the symbol
  is intentional stock KiCad, set `external: true` and supply the metadata.
- `external symbol ... not found in KiCad libraries`. The symbol LIB_ID does not
  exist in the discovered stock/extra libraries; fix the LIB_ID or point
  `--kicad-symbols-dir` at the correct library directory.
- `footprint '<Nick>:<Name>' not found`. The expected path is
  `footprints/<Nick>.pretty/<Name>.kicad_mod`.
- `3D model not found`. The rewritten path does not exist on disk.
- `unsupported variable in model path`. Replace the variable with
  `${KICAD_LIBS}`.
- `duplicate part id` / `duplicate MPN`. Reuse one part entry and list the
  MPN under the correct footprint instead of creating a second entry.
- `no footprints` / `has no MPNs`. Every part needs footprints and every
  footprint needs at least one MPN.
- `height_mm must be a positive number`. Use a numeric millimetre value
  (`1.6`), not a string (`"1.6"`) or a non-positive value.
- KiCad still shows old rows. Rebuild the database and restart KiCad.

## Done criteria

- Symbol, all footprints and all 3D models exist at the expected paths.
- External symbols are marked `external: true` and carry explicit metadata.
- Every model path uses `${KICAD_LIBS}` and the file exists.
- `parts.yaml` validates; every part explicitly carries the symbol's `Value`,
  `Description`, `Datasheet`, `Keywords` and footprint; IDs and MPNs are unique.
- Every MPN records `height_mm` when a height is known from the datasheet or
  3D model.
- `python3 build_parts_db.py` succeeds with the expected `built ...` line.
- Tests pass, and `nix flake check ...` passes when available.
- No generated files are staged.
- The change is on a branch and, if requested, ready for a pull request
  against `master`.
