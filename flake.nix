{
  description = "Drag and drop files into a shell's working directory, local or over ssh";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      home-manager,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # Holds the watcher, for a kitty.conf written by hand.
      packages = forAllSystems (pkgs: {
        default = pkgs.runCommand "ttydnd" { } ''
          install -Dm444 ${./kitty/drop.py} $out/share/ttydnd/drop.py
        '';
      });

      homeModules.default = import ./nix/home-module.nix { drop = ./kitty/drop.py; };

      checks = forAllSystems (
        pkgs:
        let
          example = home-manager.lib.homeManagerConfiguration {
            inherit pkgs;
            modules = [
              self.homeModules.default
              {
                home = {
                  username = "example";
                  homeDirectory = "/home/example";
                  stateVersion = "24.11";
                };
                programs.kitty.enable = true;
                programs.ttydnd = {
                  enable = true;
                  hyperlinkAlias = "lsh";
                };
              }
            ];
          };
          kittyConf = example.config.programs.kitty.extraConfig;
          hasLine = line: pkgs.lib.hasInfix line kittyConf;
        in
        {
          shell = pkgs.runCommand "ttydnd-shell-check" {
            nativeBuildInputs = [
              pkgs.python3
              pkgs.bash
              pkgs.zsh
              pkgs.dash
              pkgs.busybox
            ];
          } ''
            cp -r ${./.}/. src && chmod -R +w src
            cd src && python3 tests/run.py && touch $out
          '';

          module =
            assert hasLine "watcher /nix/store";
            assert hasLine "mouse_map left press ungrabbed mouse_selection drag_or_normal_select";
            assert example.config.home.shellAliases.lsh == "ls --hyperlink=auto";
            pkgs.runCommand "ttydnd-module-check" { } "touch $out";
        }
      );

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.python3
            pkgs.dash
            pkgs.busybox
            pkgs.zsh
          ];
        };
      });
    };
}
