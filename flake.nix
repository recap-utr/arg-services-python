{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    systems.url = "github:nix-systems/default";
  };
  outputs =
    inputs@{
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
          ...
        }:
        let
          python = pkgs.python3;
          packages = [
            pkgs.uv
            pkgs.buf
            python
          ];
        in
        {
          packages.release-env = pkgs.buildEnv {
            name = "release-env";
            paths = packages;
          };
          devShells.default = pkgs.mkShell {
            inherit packages;
            LD_LIBRARY_PATH = lib.makeLibraryPath (with pkgs; [ stdenv.cc.cc ]);
            UV_PYTHON = python;
            shellHook = ''
              uv sync --all-extras --locked
            '';
          };
        };
    };
}
