# Contributing

`master` is protected. Do not push to it directly. All changes land through a pull request.

## Workflow

1. Create a branch named after the part or area you are adding or editing:

   ```sh
   git switch -c stm32g474ret6
   ```

2. Make your change following [Adding / updating a part](README.md#adding--updating-a-part).
3. Build and test locally:

   ```sh
   python3 build_parts_db.py
   python3 -m unittest discover -s tests -t . -v
   ```

   The builder must print `built build/parts.sqlite (N MPN rows)`.
   For full KiCad validation run
   `nix flake check --keep-going --option max-jobs 1`.
   `build/` is generated and must not be committed.
4. Push your branch and open a pull request against `master`.
5. Wait for CI to pass. The repo owner will review and merge the pull request.

Keep one part or logical change per branch and pull request.
See [README.md](README.md) for build, setup and part-authoring details.

Thank you for contributing!
