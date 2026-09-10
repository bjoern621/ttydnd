# Wire format

Everything below travels over the tty the terminal already owns.
No port is opened, no second connection is made, and nothing is written to the remote except the dropped files.

The terminal drives every step.
The remote only ever runs the two shell lines the terminal types at its prompt.

![The three phases of a remote drop](flow.svg)

## Probe

Sent the moment a drop lands on a window whose foreground process is `ssh`.
A leading space keeps the line out of history in shells that ignore space-prefixed commands.

```sh
 command -v base64 >/dev/null && command -v tar >/dev/null && { r=ok; for n in NAMES; do
 case $n in ?*.*) b=${n%.*}; e=.${n##*.};; *) b=$n; e=;; esac;
 t=$n; i=1; while [ -e "$t" ]; do t=$b-$i$e; i=$((i+1)); done; r=$r/$t; done;
 printf '\033]1337;SetUserVar=kdrop=%s\a' "$(printf %s "$r" | base64 | tr -d '\n')"; }
```

`NAMES` is the list of dropped basenames, shell quoted.
The probe answers two questions in one round trip:

- Can this end receive at all.
  A shell that lacks `base64` or `tar` never reaches the `printf`, and the payload is never typed.
  A remote that is not a POSIX shell never runs the line at all.
- Which name is free for each dropped name.
  The loop counts up from `name-1` until nothing exists at that path.

## Reply

```
OSC 1337 ; SetUserVar=kdrop=<base64> BEL
```

The decoded value is `ok` followed by one free name per dropped name, in order, joined by `/`:

```
ok/notes-1.txt/data-1
```

A basename holds no `/`, so the join needs no escaping.
The terminal compares each free name against the name that was dropped.
A difference is a collision, and the confirmation dialog names it.

`SetUserVar` carries the reply because kitty and iTerm2 both raise an event when one arrives.
Nothing ever reads the variable back.

## Payload

```sh
 stty -echo; base64 -d | tar -xf -; s=$?; stty echo;
 [ $s = 0 ] && printf '\033]1337;SetUserVar=kdrop=ZG9uZQ==\a'
 || printf '\033]1337;SetUserVar=kdrop=ZmFpbA==\a'
```

Then a tar archive, base64 encoded, wrapped at 76 columns, terminated by `0x04` at the start of a line.
Archive member names are the free names the probe reported, so a rename needs nothing on the remote.

`stty -echo` runs before the payload and matters more than it looks.
Without it the remote tty echoes every byte back, doubling the traffic and flooding the screen.
It is restored on the same line, so it comes back even if the transfer dies.

The base64 alphabet holds no `^C`, `^D`, `^S`, `^Q`, `^H` or `^U`, so canonical mode passes it through untouched and software flow control never triggers.

Line width is not cosmetic.
Lines reaching the 4096-byte canonical input buffer lose data silently.
The 76 columns GNU `base64` writes by default sit far under `MAX_CANON`, which POSIX puts at 255.

## Result

`ZG9uZQ==` decodes to `done`, `ZmFpbA==` to `fail`, both carried by the same user var.
`$?` is tar's status, so a stream tar cannot read reports `fail` rather than staying silent.
The values are constants rather than a subshell, which keeps the line short and needs no second `base64` call.

## Timeout

A remote that answers nothing within three seconds is treated as unable to receive.
The drop then falls back to whatever the terminal does with a dropped path on its own, which for kitty is pasting it at the prompt.

## Porting

The shell half above is terminal agnostic.
A backend needs four things from its terminal:

- an event when files are dropped on a window
- a way to write to the child pty
- an event when `OSC 1337 ; SetUserVar` arrives
- a way to tell that a full-screen program owns the screen, so a drop onto `vim` is never answered with typing

kitty supplies all four.
WezTerm supplies the first three through `user-dropped-paths` and `user-var-changed`.
