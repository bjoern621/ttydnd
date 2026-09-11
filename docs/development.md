# Running the suite

```sh
nix flake check
```

The suite drives real shells on real ptys, using whichever of `sh`, `bash`, `zsh`, `dash` and `ash` are on `PATH`.
The flake check adds the Home Manager module to that.
`python3 tests/run.py` runs the suite alone, and `nix develop` provides the shells.

Python and the remote shell resolve free names independently.
The suite runs both and compares them, since a mismatch would make the dialog lie.

## kitty internals

The kitty backend replaces `Window.on_drop` and drives the progress marker through kitty's own classes, so a kitty release can move either.
Where the backend sits and what it owes the other domains: [terminal-backends.md](terminal-backends.md).
