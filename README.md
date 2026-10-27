# kicad-libs

Shared KiCad 10 symbol / footprint library.

Check out available parts at <https://sid115.github.io/kicad-libs/>.

The catalogue is a KiCad database library.
`parts.yaml` in this repo is the source of truth for parts, footprints and MPNs.
`build_parts_db.py` validates it against the symbol, footprint and 3D-model files and generates `build/parts.sqlite`.
KiCad reads that database through `parts.kicad_dbl`, registered in the shared `sym-lib-table`.
CI builds the database and attaches it to a release, but every user can also build it locally.

## Building the parts database

Build `build/parts.sqlite` from `parts.yaml`.

### Nix (recommended)

```sh
nix run .
```

### Pip

With Python 3.11+:

```sh
python3 -m pip install -r requirements.txt
python3 build_parts_db.py
```

## One-time setup for each user

KiCad 10 is required.

1. Clone the repo to a fixed path (e.g. `C:\kicad-libs` / `~/kicad-libs`):
   ```sh
   git clone https://git.sid.ovh/sid/kicad-libs.git ~/kicad-libs
   ```

2. Define a path variable so all paths resolve the same on every machine:
   - *Preferences -> Configure Paths...* -> add `KICAD_LIBS` = the clone path.

3. Register the shared symbol table:
   - *Preferences -> Manage Symbol Libraries...* -> *Add empty row to table*
   - Library Path: `${KICAD_LIBS}/sym-lib-table`
   - *Open Library Table* to verify

4. Register the shared footprint table:
   - *Preferences -> Manage Footprint Libraries...* -> *Add empty row to table*
   - Library Path: `${KICAD_LIBS}/fp-lib-table`
   - *Open Library Table* to verify

## Adding / updating a part

1. Add or edit the symbol under `symbols/`, its footprint under `footprints/`, and its 3D model under `3dmodels/`:
   - The symbol nickname must match the `.kicad_sym` filename stem, and the footprint nickname must match the `.pretty` directory name.
   - `Value`, `Footprint`, `Datasheet`, `Description` and `Keywords` may be authored in either the symbol or `parts.yaml` (recommended). YAML values win. Omitted fields inherit the symbol value.
   - To reference a stock KiCad symbol such as `Device:R` instead of adding it under `symbols/`, set `external: true` on the part and supply `value`, `description`, `datasheet` and `keywords` explicitly.
   - Reference the model from the footprint as `${KICAD_LIBS}/3dmodels/<name>.stp` (or `.step`). No other `${...}` variables are supported.
   - If (and only if) you create a new library, register it in `sym-lib-table` / `fp-lib-table` with the same nickname. In `sym-lib-table`:

     ```lisp
     (lib (name "<Nick>") (type "KiCad") (uri "${KICAD_LIBS}/symbols/<Nick>.kicad_sym") (options "") (descr "..."))
     ```

     In `fp-lib-table`:

     ```lisp
     (lib (name "<Nick>") (type "KiCad") (uri "${KICAD_LIBS}/footprints/<Nick>.pretty") (options "") (descr "..."))
     ```

2. Add or edit the part entry in `parts.yaml` with its symbol LIB_ID, one or more footprints, and one or more MPNs:
   - `id` is required and unique. MPNs are globally unique.
   - Every footprint needs at least one MPN.
   - `footprint` may be omitted for a single-footprint part. It then defaults to the symbol's `Footprint`.
   - `description` and `datasheet` may be overridden per MPN, other fields are inherited.
   - `height_mm` is optional and recorded per MPN, in millimetres.

3. Validate and rebuild locally:
   ```sh
   python3 build_parts_db.py
   python3 -m unittest discover -s tests -t . -v
   ```
   The builder must print `built build/parts.sqlite (N MPN rows)`. For full KiCad validation run:
   ```sh
   nix flake check --keep-going --option max-jobs 1
   ```
   `build/` is generated and must not be committed.

4. Push to a branch and open a merge request against `master`. CI runs the tests on the pull request and publishes the parts database from `master`. See [CONTRIBUTING.md](./CONTRIBUTING.md).

5. Everyone else pulls and re-runs the builder (or downloads the parts database from the release page). No table re-registration is needed. Restart KiCad if the database cache still shows old rows.

## Field precedence

| Database field | Resolution order |
| --- | --- |
| `value` | YAML part `value` -> symbol `Value` |
| `description` | MPN `description` -> YAML part `description` -> symbol `Description` |
| `datasheet` | MPN `datasheet` -> YAML part `datasheet` -> symbol `Datasheet` |
| `keywords` | YAML part `keywords` -> symbol `Keywords` |
| `footprint` | YAML footprint entry -> symbol `Footprint` |
| `height_mm` | MPN `height_mm` only, no fallback |

When a part-level field is present in both YAML and the symbol and they differ, the builder prints a warning and uses the YAML value.
MPN-level `description` and `datasheet` overrides never warn, since they are intentional.
Footprint mismatches warn only when exactly one footprint is listed.
Multiple footprints are treated as a deliberate mapping.

## MPN variants

One symbol can serve many MPNs and several footprints. `parts.yaml` groups them:

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

Each MPN belongs to the footprint for its exact package.
The builder emits one database row per MPN.

## Browsing the parts catalogue

To build the site locally:

```sh
python3 build_site.py
python3 -m http.server --directory build/site
```

Then open <http://localhost:8000/>.
