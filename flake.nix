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
          config,
          ...
        }:
        let
          uv = pkgs.uv;
          python = pkgs.python312;
          packages = [
            uv
            python
            config.packages.buf-generate
          ];
        in
        {
          packages = {
            release-env = pkgs.buildEnv {
              name = "release-env";
              paths = packages;
            };
            buf-generate = pkgs.writeShellApplication {
              name = "buf-generate";
              runtimeInputs = with pkgs; [
                protobuf
                mypy-protobuf
              ];
              text = ''
                ${lib.getExe pkgs.buf} generate
                find src/* -type d -exec touch {}/__init__.py \;
                cp -f arg_services/__init__.py src/arg_services/__init__.py
              '';
            };
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
