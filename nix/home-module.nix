{ drop }:

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
      default = "ls";
      example = "lsh";
      description = ''
        Name for an `ls --hyperlink=auto` alias, which marks file names as drag sources.
        Aliases spelled in terms of `ls`, such as `ll`, pick the flag up through the shell's own alias expansion.
        An alias of the same name set elsewhere wins.
        Null adds no alias.
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
      watcher ${drop}
    ''
    + lib.optionalString cfg.dragOut ''
      mouse_map left press ungrabbed mouse_selection drag_or_normal_select
    '';

    home.shellAliases = lib.mkIf (cfg.hyperlinkAlias != null) {
      ${cfg.hyperlinkAlias} = lib.mkDefault "ls --hyperlink=auto";
    };
  };
}
