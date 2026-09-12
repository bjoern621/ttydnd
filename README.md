# ttydnd

Drag and drop for the kitty terminal.
A file dropped on a window lands in the directory the shell is in, local or over ssh.
The files travel over the open ssh session, so the remote needs nothing installed.

![License GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)
![kitty 0.48+](https://img.shields.io/badge/kitty-0.48%2B-green)

![Dropping a file on a shell over ssh, and the dialog that asks before writing](docs/dialog.svg)

- A plain `ssh` session takes drops at its prompt, with no kitten to run first.
- Any host with a POSIX shell, `tar` and `base64` receives, busybox included.
- So does a nested ssh, or a `sudo -i` on the far side.
- Directories drop whole, and permissions travel with the files.
- Nothing is written until every item's dialog is answered.
- A probe checks that the host can unpack before the files go out.
- A file name on screen drags out to another window or a file manager.

## Install

Runs on Linux and macOS, in kitty 0.48 or newer with shell integration on, which is kitty's default.

### Home Manager

```nix
{
  inputs.ttydnd.url = "github:bjoern621/ttydnd";

  # in the home-manager configuration
  imports = [ inputs.ttydnd.homeModules.default ];
  programs.kitty.enable = true;
  programs.ttydnd.enable = true;
}
```

### kitty alone

```sh
git clone https://github.com/bjoern621/ttydnd
cp -r ttydnd/kitty/drop.py ttydnd/ttydnd ~/.config/kitty/
cat >> ~/.config/kitty/kitty.conf <<'EOF'
watcher drop.py
mouse_map left press ungrabbed mouse_selection drag_or_normal_select
EOF
```

The `mouse_map` line enables drag out, and a drag over plain text selects it.
`ctrl+shift+f5` (`ctrl+cmd+,` on macOS) reloads the config, and the next window opened takes drops.

## Use

A drop on a shell at its prompt opens one dialog per item.
Over ssh, a tmux on the remote needs `allow-passthrough on`.
`ls --hyperlink=auto` marks the names it prints, and a drag on one carries the file out.

## Configuration

| Option | Home Manager | By hand |
| --- | --- | --- |
| Drag out | `programs.ttydnd.dragOut`, on by default | `mouse_map` line in `kitty.conf` |
| Draggable names | `programs.ttydnd.hyperlinkAlias`, `"ls"` by default | `alias ls='ls --hyperlink=auto'` |
| Seconds to wait for the remote's answer | `programs.ttydnd.timeout`, `3` by default | `TIMEOUT` in `ttydnd/copy/tty.py` |

`--hyperlink` is a GNU `ls` option, so a stock macOS goes without the alias: `hyperlinkAlias = null`.

Internals: [docs/architecture.md](docs/architecture.md), [docs/protocol.md](docs/protocol.md), [docs/development.md](docs/development.md).

## License

GPL-3.0.
