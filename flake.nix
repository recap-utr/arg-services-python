{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    systems.url = "github:nix-systems/default";
  };
  outputs = inputs @ {
    self,
    nixpkgs,
    flake-parts,
    systems,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = import systems;
      perSystem = {
        pkgs,
        lib,
        system,
        self',
        ...
      }: let
        poetry = pkgs.poetry;
        python = pkgs.python311;
        packages = with pkgs; [poetry python buf protobuf mypy-protobuf];
      in {
        packages = {
          releaseEnv = pkgs.buildEnv {
            name = "release-env";
            paths = packages;
          };
        };
        devShells.default = pkgs.mkShell {
          inherit packages;
          POETRY_VIRTUALENVS_IN_PROJECT = true;
          LD_LIBRARY_PATH = lib.makeLibraryPath (with pkgs; [stdenv.cc.cc]);
          shellHook = ''
            ${lib.getExe poetry} env use ${lib.getExe python}
            ${lib.getExe poetry} install --all-extras --no-root
          '';
        };
      };
    };
}
