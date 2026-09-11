{ watcher, package }:

{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.programs.ttydnd;

  # The watcher finds the package beside it, so both land in one directory.
  tree = pkgs.runCommand "ttydnd" { } ''
    mkdir -p $out
    cp ${watcher} $out/drop.py
    cp -r ${package} $out/ttydnd
    chmod -R u+w $out
    grep -q '^TIMEOUT = ' $out/ttydnd/copy/tty.py
    sed -i 's/^TIMEOUT = .*/TIMEOUT = ${toString cfg.timeout}/' $out/ttydnd/copy/tty.py
  '';
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

    timeout = lib.mkOption {
      type = lib.types.numbers.positive;
      default = 3;
      example = 10;
      description = ''
        Seconds a probe waits for the remote's reply before the drop falls back to kitty's own handling.
        A link with hundreds of milliseconds of latency needs more, since the reply arrives behind the shell's echo of the probe.
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
      watcher ${tree}/drop.py
    ''
    + lib.optionalString cfg.dragOut ''
      mouse_map left press ungrabbed mouse_selection drag_or_normal_select
    '';

    home.shellAliases = lib.mkIf (cfg.hyperlinkAlias != null) {
      ${cfg.hyperlinkAlias} = lib.mkDefault "ls --hyperlink=auto";
    };
  };
}
