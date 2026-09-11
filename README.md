# ttydnd

Drag and drop for the kitty terminal.
A file dropped on a window lands in the directory the shell is in.
Over ssh it lands on the remote host, in that shell's directory, with nothing installed there.

![License GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)
![kitty 0.48+](https://img.shields.io/badge/kitty-0.48%2B-green)

![Dropping a file on a shell over ssh, and the dialog that asks before writing](docs/dialog.svg)

One dialog per item, and the files are there.

## What it does

- Files and directories land where the prompt is, with their permissions intact,
  so a deep path on a remote host takes one drag.
- Any ssh host receives.
  A POSIX shell with `tar` and `base64` is all it takes,
  and every stock Linux, macOS or Alpine host ships that.
- The files travel inside the ssh session that is already open,
  so a jump host, a nested ssh or a `sudo -i` on the far side receives the same way.
- Safe to try on any host.
  A shell that cannot receive never sees the files, and nothing is written until every item is answered.
- A taken name gets its own dialog before anything is written:
  Keep both, Overwrite or Skip, with the free name spelled out.
- A file name on screen carries its file back out on a drag, to another window or to a file manager.
- A full-screen program such as `vim` keeps kitty's own drop behaviour.

## Install

Needs kitty 0.48 or newer with shell integration on, which is kitty's default.

### Home Manager

```nix
{
  inputs.ttydnd.url = "github:bjoern621/ttydnd";

  # in the home-manager configuration
  imports = [ inputs.ttydnd.homeModules.default ];
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

`nix build github:bjoern621/ttydnd` puts the same pair under `result/share/ttydnd/` for a config written by hand.

Windows opened after a config reload take drops.
`ctrl+shift+f5` reloads the config on Linux and `ctrl+cmd+,` on macOS.

## Dropping files in

A drop on a shell sitting at its prompt opens one dialog per item,
naming the item and its size, and asking Copy or Skip.
The last answer starts the copy, and the files land in the working directory the shell reports.
Over ssh they land on the host the shell is logged into, in its working directory.
A finished copy is silent.
A failure shows in an overlay on the window until Enter, Esc or a click closes it.

Where a name is already taken, that item's dialog offers Keep both, Overwrite or Skip,
and spells out the free name.
Keep both counts up from `notes-1.txt` until a name is free,
and the file already there keeps its name and its content.
Overwrite on a directory merges into it, replacing the files whose names clash and leaving the rest.
Two dropped items sharing a basename get the second one suffixed the same way,
so a drop never loses an item.
Esc on any item cancels the whole drop.

The dialog takes Enter for the default, the highlighted letter, or a click on a button.
An open dialog takes its answer before the window takes another drop.
A drop onto a failure overlay closes it and goes ahead.
While a transfer runs,
kitty's progress marker shows in the tab bar and its progress bar on the window edge.

A shell busy running a command, and a full-screen program such as `vim`,
get kitty's own drop behaviour: the paths pasted as text.
A remote that stays silent past the timeout gets the same, and an overlay says so.
A symlink dropped on a local shell copies what it points at.
Over ssh the link itself travels.

## Dragging files out

A drag that starts on a file name in the output carries that file to another window
or to a file manager.

The name has to be a hyperlink for there to be anything to drag.
`ls --hyperlink=auto` marks its names.
An alias spelled in terms of `ls`, such as `ll`,
picks the flag up through the shell's own alias expansion.
`auto` limits the markup to a terminal, so pipes and scripts read plain output.
`kitten hyperlinked_grep` carries the hyperlinks already.
`--hyperlink` needs GNU coreutils 8.30 or newer.

## Configuration

A Home Manager option and a hand-written line reach the same place.

| What it sets | Home Manager | By hand |
| --- | --- | --- |
| The watcher | `programs.ttydnd.enable` | the `watcher` line in `kitty.conf` |
| Carrying a file out on a drag | `programs.ttydnd.dragOut`, on by default | the `mouse_map` line in `kitty.conf` |
| File names as drag sources | `programs.ttydnd.hyperlinkAlias`, `"ls"` by default | `alias ls='ls --hyperlink=auto'` in a shell rc |
| Seconds to wait for a remote's answer | `programs.ttydnd.timeout`, `3` by default | `TIMEOUT` in `ttydnd/copy/tty.py` |

A slow link wants a longer timeout.

`hyperlinkAlias = null` adds no alias, which is what a BSD `ls` wants.
Any other value names the alias instead of `ls`.
An alias of the same name set elsewhere wins.

## Where it works

A remote needs a POSIX shell with `base64`, `tar`, `cat`, `stty` and `printf`,
which coreutils and busybox both ship.
`sh`, `bash`, `zsh`, `dash` and busybox `ash` all receive.

A window whose foreground command is `ssh` counts as remote, whatever shell answers on the far side.
A window running another remote shell, such as `mosh` or `docker exec`, gets kitty's own drop behaviour.
tmux on the remote passes the answer back with `allow-passthrough on`.

The lines typed at the remote start with a space,
so a shell set to ignore space-prefixed commands keeps them out of history.
That setting is `HISTCONTROL=ignorespace` in bash and `setopt histignorespace` in zsh.

Transfers run at a few megabytes per second,
so a source tree or a folder of photos crosses in seconds.
Gigabytes are a job for `scp`.

## Under the hood

The files travel as a tar archive typed at the remote prompt,
after a one-line probe has checked that the host can receive and which names are free.
[docs/protocol.md](docs/protocol.md) has the wire format,
[docs/architecture.md](docs/architecture.md) the parts and what crosses between them,
and [docs/development.md](docs/development.md) the test suite.

## License

GPL-3.0-only.
