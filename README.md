# ttydnd

Files dropped on a kitty window land in the shell's working directory.
Over ssh they land on the remote, which needs nothing installed.

![License GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)
![kitty 0.48+](https://img.shields.io/badge/kitty-0.48%2B-green)

![Dropping a file on a shell over ssh, and the dialog that asks before writing](docs/dialog.svg)

The files ride the tty that ssh already owns, as a tar stream typed at the prompt.
No agent, no port, no second connection, nothing written to the remote except the dropped files.
Whatever shell answers on the far side takes the drop, so a nested ssh or a `sudo -i` beyond it works the same.

## Features

- Files and directories land in the working directory the shell reports, with their permissions.
- An ssh session is the whole transport, so the remote needs a POSIX shell with `base64` and `tar`.
- Nothing is written until every item is answered, and a name clash offers Keep both, Overwrite or Skip.
- An unknown host costs nothing to try, since a shell that cannot receive never sees the files.
- A file name in the output is a drag source, and carries the file out to another window or a file manager.
- A full-screen program keeps kitty's own drop behaviour, so `vim` is never typed at.

## Requirements

| Where | Needs |
| --- | --- |
| Local | kitty 0.48 or newer, with shell integration on |
| Remote | a POSIX shell plus `base64`, `tar`, `cat`, `stty` and `printf` |

Shell integration is kitty's default, and it reports the working directory and whether the shell sits at a prompt.
Every remote requirement ships in coreutils and in busybox, so a stock Linux, macOS or Alpine host already qualifies.
Verified on `sh`, `bash`, `zsh`, `dash` and busybox `ash`.

## Install

The watcher is a Python file kitty loads per window, and it finds the rest of ttydnd in the package beside it.
Watchers attach when a window is created, so open windows keep kitty's own drop behaviour until the next one opens.

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

## Configuration

A Home Manager option and a hand-written line reach the same place.

| What it sets | Home Manager | By hand |
| --- | --- | --- |
| The watcher | `programs.ttydnd.enable` | the `watcher` line in `kitty.conf` |
| Carrying a file out on a drag | `programs.ttydnd.dragOut`, on by default | the `mouse_map` line in `kitty.conf` |
| File names as drag sources | `programs.ttydnd.hyperlinkAlias`, `"ls"` by default | `alias ls='ls --hyperlink=auto'` in a shell rc |
| Seconds a probe waits for the remote | `programs.ttydnd.timeout`, `3` by default | `TIMEOUT` in `ttydnd/copy/tty.py` |

A link with hundreds of milliseconds of latency wants a longer timeout, since the reply arrives behind the shell's echo of the probe.

`hyperlinkAlias = null` adds no alias, which is what a BSD `ls` wants.
Any other value names the alias instead of `ls`.
An alias of the same name set elsewhere wins.

## Dropping files in

| Situation | Result |
| --- | --- |
| Local shell at a prompt | A dialog per item, then a copy into the working directory the shell reports |
| Shell over ssh | A dialog per item, then a tar stream typed at the prompt |
| Local shell busy running something | kitty's own handling, which pastes the paths |
| A full-screen program owns the screen | kitty's own handling, so `vim` is never typed at |
| A name is already taken | That item's dialog offers Keep both, Overwrite or Skip, and names the free name |
| Two dropped items share a basename | The watcher suffixes the second, so a drop never loses an item |
| The remote stays silent past the timeout | kitty's own handling, and an overlay saying so |

A drop counts as remote when `ssh` is one of the window's foreground processes.

Each item gets a dialog naming it and its size, before anything is written.
A free name asks Copy or Skip.
Keep both counts up from `notes-1.txt` until the name is free, on either end.
The file already sitting there keeps its name and its content.
Overwrite on a directory merges into it, replacing the files whose names clash and leaving the rest.
Skip leaves that item alone.
Esc on any item cancels the whole drop.

The dialog takes Enter for the default, the highlighted letter, a click on a button, or Esc.
kitty's ask kitten binds no arrow keys.

A copy that lands shows nothing.
A failure shows in an overlay on the window until Enter, Esc or a click closes it.
A drop onto that overlay closes it and goes ahead.
A drop onto an open dialog waits for that dialog to be answered.

A probe or a transfer in flight shows as kitty's progress marker in the tab bar and its progress bar on the window edge.
An OSC 9;4 report drives the same signal.
The marker clears once the files land, and marks an error when a copy or an unpack fails.

## Dragging files out

A drag that starts on a hyperlink carries that file to another window or to a file manager.

Output has to carry the hyperlinks for there to be anything to drag.
`ls --hyperlink=auto` marks its names, and an alias spelled in terms of `ls`, such as `ll`, picks the flag up through the shell's own alias expansion.
`auto` limits the markup to a terminal, so pipes and scripts read the plain output.
`kitten hyperlinked_grep` carries the hyperlinks already.
`--hyperlink` needs GNU coreutils 8.30 or newer.

## How it reaches a remote

![The three phases of a remote drop](docs/flow.svg)

A probe goes first and answers two questions in one round trip: whether this end can receive, and which name is free for each dropped name.
Silence means the files are never sent, so pointing at an unknown host is safe.

[docs/protocol.md](docs/protocol.md) has the wire format, and the reasons behind the parts that look arbitrary.

## Limits

- A drop counts as remote only when `ssh` is a foreground process, so `mosh`, `docker exec` and a serial console never take the remote path.
- Throughput is roughly 1 to 5 MB/s over ssh, so a multi-gigabyte drop is the wrong tool.
- A local drop copies what a symlink points at, and a remote drop keeps the link.
- The remote shell records the probe line, and the receive line once an item is confirmed. A leading space keeps both out of history in bash with `HISTCONTROL=ignorespace` and zsh with `setopt histignorespace`.
- tmux on the remote needs `allow-passthrough` before the probe's answer gets back, though the files themselves travel fine.
- `Window.on_drop` and the progress marker are kitty internals, so a kitty release can move them.

## Tests

```sh
nix flake check
```

The suite drives real shells on real ptys, using whichever of `sh`, `bash`, `zsh`, `dash` and `ash` are on `PATH`.
The flake check adds the Home Manager module to that.
`python3 tests/run.py` runs the suite alone, and `nix develop` provides the shells.

Python and the remote shell resolve free names independently.
The suite runs both and compares them, since a mismatch would make the dialog lie.

## Porting

Dropping, confirming and copying are three domains, and [docs/architecture.md](docs/architecture.md) states what crosses between them.
A terminal backend is the first of them, and [docs/terminal-backends.md](docs/terminal-backends.md) lists what it needs from its terminal.
WezTerm has all but the alternate screen check through `user-dropped-paths` and `user-var-changed`.
Terminals with no scripting hook, such as foot, alacritty and ghostty, cannot host the watcher.

## License

GPL-3.0-only.
The watcher imports kitty's Python modules, and kitty is GPL-3.0.
