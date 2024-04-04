{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    systems.url = "github:nix-systems/default";
  };
  outputs =
    inputs@{
      self,
      nixpkgs,
      flake-parts,
      systems,
      ...
    }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = import systems;
      perSystem =
        {
          pkgs,
          lib,
          system,
          self',
          ...
        }:
        let
          poetry = pkgs.poetry;
          python = pkgs.python311;
          packages = [
            poetry
            python
          ];
        in
        {
          packages = {
            releaseEnv = pkgs.buildEnv {
              name = "release-env";
              paths = packages;
            };
            bufGenerate = pkgs.writeShellApplication {
              name = "buf-generate";
              runtimeInputs = with pkgs; [ mypy-protobuf ];
              text = ''
                ${lib.getExe pkgs.buf} mod update &&
                ${lib.getExe pkgs.buf} generate --include-imports buf.build/recap/arg-services &&
                find src -type d -exec touch {}/__init__.py \; &&
                rm -f src/__init__.py &&
                cp -rf arg_services/__init__.py src/arg_services/__init__.py
              '';
            };
          };
          devShells.default = pkgs.mkShell {
            inherit packages;
            POETRY_VIRTUALENVS_IN_PROJECT = true;
            LD_LIBRARY_PATH = lib.makeLibraryPath (with pkgs; [ stdenv.cc.cc ]);
            shellHook = ''
              ${lib.getExe poetry} env use ${lib.getExe python}
              ${lib.getExe poetry} install --all-extras --no-root
            '';
          };
        };
    };
}
