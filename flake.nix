{
  description = "Shared KiCad libraries and parts database builder";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      forAllSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          f {
            inherit system;
            pkgs = nixpkgs.legacyPackages.${system};
          }
        );

      python = pkgs: pkgs.python312.withPackages (ps: [ ps.pyyaml ]);
    in
    {
      packages = forAllSystems (
        { pkgs, ... }:
        let
          build-parts-db = pkgs.writeShellApplication {
            name = "build-parts-db";
            runtimeInputs = [ (python pkgs) ];
            text = ''
              exec python ${./build_parts_db.py} \
                --parts "$PWD/parts.yaml" \
                --output "$PWD/build/parts.sqlite" \
                --root "$PWD" \
                "$@"
            '';
          };

          import-lcsc = pkgs.writeShellApplication {
            name = "import-lcsc";
            runtimeInputs = [ (python pkgs) pkgs.easyeda2kicad ];
            text = ''
              exec python ${./import_lcsc.py} --root "$PWD" "$@"
            '';
          };

          site = pkgs.runCommand "kicad-libs-site"
            {
              nativeBuildInputs = [ (python pkgs) ];
            }
            ''
              cp -r ${self} source
              cd source
              python build_site.py --parts parts.yaml --root . --output "$out"
            '';
        in
        {
          inherit build-parts-db;
          inherit import-lcsc;
          inherit site;
          default = build-parts-db;
        }
      );

      devShells = forAllSystems (
        { pkgs, system }:
        {
          default = pkgs.mkShell {
            packages = [
              (python pkgs)
              self.packages.${system}.default
              self.packages.${system}.import-lcsc
              pkgs.easyeda2kicad
            ];

            shellHook = ''
              export KICAD10_SYMBOL_DIR="${pkgs.kicad-small.passthru.libraries.symbols}/share/kicad/symbols"
            '';
          };
        }
      );

      checks = forAllSystems (
        { pkgs, system }:
        let
          kicadCheck =
            name: script:
            pkgs.runCommand name
              {
                nativeBuildInputs = [ pkgs.kicad-small ];
              }
              ''
                set -euo pipefail
                export HOME="$TMPDIR"

                cp -r ${self} source
                cd source

                ${script}

                touch $out
              '';
        in
        {
          site = self.packages.${system}.site;

          tests =
            pkgs.runCommand "kicad-libs-tests"
              {
                nativeBuildInputs = [ (python pkgs) ];
                KICAD10_SYMBOL_DIR = "${pkgs.kicad-small.passthru.libraries.symbols}" + "/share/kicad/symbols";
              }
              ''
                cp -r ${self} source
                cd source
                python -m unittest discover -s tests -t . -v
                touch $out
              '';

          symbols-valid = kicadCheck "kicad-symbols-valid" ''
            shopt -s nullglob globstar
            mkdir -p "$TMPDIR/upgraded"
            for lib in symbols/**/*.kicad_sym; do
              echo "validating symbol library $lib"
              kicad-cli sym upgrade --force \
                --output "$TMPDIR/upgraded/$(basename "$lib")" "$lib"
            done
          '';

          symbols-svg = kicadCheck "kicad-symbols-svg" ''
            shopt -s nullglob globstar
            for lib in symbols/**/*.kicad_sym; do
              svgdir="$TMPDIR/svg/$(basename "$lib" .kicad_sym)"
              mkdir -p "$svgdir"
              echo "rendering symbol library $lib"
              kicad-cli sym export svg --output "$svgdir" "$lib"
            done
          '';

          footprints-valid = kicadCheck "kicad-footprints-valid" ''
            shopt -s nullglob globstar
            mkdir -p "$TMPDIR/upgraded"
            for lib in footprints/**/*.pretty; do
              echo "validating footprint library $lib"
              kicad-cli fp upgrade --force \
                --output "$TMPDIR/upgraded/$(basename "$lib")" "$lib"
            done
          '';

          footprints-svg = kicadCheck "kicad-footprints-svg" ''
            shopt -s nullglob globstar
            for lib in footprints/**/*.pretty; do
              svgdir="$TMPDIR/svg/$(basename "$lib" .pretty)"
              mkdir -p "$svgdir"
              echo "rendering footprint library $lib"
              kicad-cli fp export svg --output "$svgdir" "$lib"
            done
          '';
        }
      );
    };
}
