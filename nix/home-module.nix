{ dir }:

{ config, lib, ... }:

let
  cfg = config.programs.ttydnd;
in
{
  options.programs.ttydnd = {
    enable = lib.mkEnableOption "dropping files into the shell's working directory";

    dragOut = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Bind left press so a drag starting on a hyperlink carries that file out to other windows.
        Text selection is unaffected everywhere else.
      '';
    };

    hyperlinkAlias = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "lsh";
      description = ''
        Name for an `ls --hyperlink=auto` alias, which marks file names as drag sources.
        Null adds no alias, leaving `ls` untouched.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.programs.kitty.enable;
        message = "programs.ttydnd needs programs.kitty.enable, since the watcher runs inside kitty.";
      }
    ];

    # extraConfig rather than settings.watcher, so other watchers keep their own lines.
    programs.kitty.extraConfig = ''
      watcher ${dir}/drop.py
    ''
    + lib.optionalString cfg.dragOut ''
      mouse_map left press ungrabbed mouse_selection drag_or_normal_select
    '';

    home.shellAliases = lib.mkIf (cfg.hyperlinkAlias != null) {
      ${cfg.hyperlinkAlias} = "ls --hyperlink=auto";
    };
  };
}
