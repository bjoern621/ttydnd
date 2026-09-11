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
      # Holds the watcher and the package beside it, for a kitty.conf written by hand.
      packages = forAllSystems (pkgs: {
        default = pkgs.runCommand "ttydnd" { } ''
          mkdir -p $out/share/ttydnd
          cp ${./kitty/drop.py} $out/share/ttydnd/drop.py
          cp -r ${./ttydnd} $out/share/ttydnd/ttydnd
        '';
      });

      homeModules.default = import ./nix/home-module.nix {
        watcher = ./kitty/drop.py;
        package = ./ttydnd;
      };

      checks = forAllSystems (
        pkgs:
        let
          mkExample =
            settings:
            home-manager.lib.homeManagerConfiguration {
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
                  programs.ttydnd = { enable = true; } // settings;
                }
              ];
            };
          example = mkExample { };
          kittyConf = example.config.programs.kitty.extraConfig;
          slowConf = (mkExample { timeout = 10; }).config.programs.kitty.extraConfig;
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
            assert example.config.home.shellAliases.ls == "ls --hyperlink=auto";
            pkgs.runCommand "ttydnd-module-check" { inherit kittyConf slowConf; } ''
              grep -qx 'TIMEOUT = 3' "$(dirname "$(printf %s "$kittyConf" | sed -n 's/^watcher //p')")/ttydnd/copy/tty.py"
              grep -qx 'TIMEOUT = 10' "$(dirname "$(printf %s "$slowConf" | sed -n 's/^watcher //p')")/ttydnd/copy/tty.py"
              touch $out
            '';
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
