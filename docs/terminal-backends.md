# Writing a terminal backend

A backend is the drop domain for one terminal:
it catches the gesture, decides whether the window can take it,
and offers the port the confirm and copy domains reach the terminal through.
This page is the code contract for writing one.
Where the domain sits, and what reaches it: [architecture.md](architecture.md).

## What the terminal has to supply

- an event when files are dropped on a window
- a way to write to the child pty
- an event when `OSC 1337 ; SetUserVar` arrives
- a way to tell that a full-screen program owns the screen, so a drop onto one keeps the terminal's own handling

kitty supplies all four.
WezTerm supplies the first three through `user-dropped-paths` and `user-var-changed`.
A terminal with no scripting hook hosts no backend.

## What a backend calls

```python
ttydnd.dropped(destination, paths)     # take a drop through confirmation and copying
ttydnd.answered(terminal_id, value)    # a user var the destination set, for the waiting transport
ttydnd.busy(terminal_id)               # True while a transport owns the window's marker
```

The rest of the package belongs to the other two domains.

## The destination a backend builds

`Destination(terminal, kind, path)` is what the backend knows about where the files go.

- `kind` is `directory` for a path this process can write, with `path` naming it.
- `kind` is `session` for a shell the terminal types at, whose working directory only the shell knows.
- `terminal` is the port below.

A backend that can tell more about a session, such as the host a connection could reach on its own,
carries it on the destination for a transport to rank on.

## The port a backend offers

```python
terminal.id                                    # stable per window, and what answers are keyed by
terminal.alive                                 # False once the window is gone
terminal.write(data)                           # to the child pty
terminal.after(seconds, run)                   # run once, later
terminal.ask(message, choices, default, answered)   # one dialog, answered with a shortcut letter
terminal.tell(text)                            # a failure, shown until dismissed
terminal.progress(state)                       # 3 works, 2 failed, 0 clear
terminal.fallback()                            # hand the drop back to the terminal
```

A call on a window that has closed does nothing.
`ask` takes choices in the shape `letter;color:Label`, delivers the shortcut letter to `answered`,
and delivers an empty answer for Esc, which cancels the whole drop.
Every dialog is opened from a deferral, since a backend runs inside the terminal's own callback.

`fallback` hands the drop back so the terminal does whatever it does with a dropped path on its own,
which for kitty is pasting it at the prompt.
That is the answer to a full-screen program, a shell busy running something,
and a destination that never replies.
