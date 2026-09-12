# Wire format

Everything below travels over the tty the terminal already owns.
The terminal drives every step, and the remote runs the two shell lines typed at its prompt.
The dropped files are all the remote writes.
One transport carries a drop this way, and [copy-transports.md](copy-transports.md) states what any transport owes.

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
  A shell lacking `base64` or `tar` stops before the `printf`, so the payload is never typed.
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
Nothing reads the variable back.

## Payload

```sh
 stty -echo; printf '\033]1337;SetUserVar=kdrop=cmR5\a'; base64 -d | (tar -xf -; e=$?;
 [ $e = 0 ] || printf '\033]1337;SetUserVar=kdrop=c3RvcA==\a';
 cat >/dev/null; exit $e); s=$?; stty echo;
 [ $s = 0 ] && printf '\033]1337;SetUserVar=kdrop=ZG9uZQ==\a'
 || printf '\033]1337;SetUserVar=kdrop=ZmFpbA==\a'
```

The line answers `rdy` the moment `base64` takes the tty, and the terminal holds the archive until that answer arrives.
An interactive zsh reads its prompt in blocks, so a payload typed before `base64` is reading lands in the line editor and runs as commands, leaving `base64` a truncated archive.
A shell that reads a line at a time takes the payload whenever it comes, so the one round trip serves every shell.

Then a tar archive, base64 encoded, wrapped at 76 columns,
terminated by `0x04` at the start of a line.
Archive member names are the free names the probe reported, so a rename needs nothing on the remote.

## Stream constraints

`tar` ends on the first member it cannot write, with the payload still arriving.
The subshell holds the pipe open and `cat` swallows what is left,
so the stream never reaches the shell.
A shell that reads it runs every line as a command.

The payload goes out in bursts of whole lines.
A terminal cannot recall what it has queued for the pty,
so a burst is what a stopped transfer still costs.
Whole lines keep a truncated stream decodable and put the terminator at a line start.

`stty -echo` runs before the payload,
since the remote tty would otherwise echo every byte back, doubling the traffic and flooding the screen.
It is restored on the same line, so it comes back even if the transfer dies.

The base64 alphabet holds no `^C`, `^D`, `^S`, `^Q`, `^H` or `^U`,
so canonical mode passes it through untouched and software flow control never triggers.

Lines reaching the 4096-byte canonical input buffer lose data silently.
The 76 columns GNU `base64` writes by default sit far under `MAX_CANON`, which POSIX puts at 255.

## Result

`cmR5` decodes to `rdy`, `c3RvcA==` to `stop`, `ZG9uZQ==` to `done` and `ZmFpbA==` to `fail`, all carried by the same user var.

`stop` goes out the moment tar exits non-zero, with the drain about to start.
The terminal answers it by dropping the rest of the payload and typing the `0x04` terminator,
which ends the drain there.
A `0x04` at a prompt closes the session, so it is typed only while the drain is reading.

`done` and `fail` arrive once the stream is consumed and the prompt is back.
`$?` is tar's status, so a stream tar cannot read reports `fail`.
The three answers are literal constants, which keeps the line short.

## Timeout

A remote that answers nothing within the timeout, three seconds by default, counts as unable to receive.
The drop then falls back to whatever the terminal does with a dropped path on its own,
which for kitty is pasting it at the prompt.

## Porting

The shell half above is terminal agnostic.
What a terminal has to supply for a backend to host it: [terminal-backends.md](terminal-backends.md).
