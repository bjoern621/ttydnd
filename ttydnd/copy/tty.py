"""Transport: a base64 tar archive typed at the destination shell's prompt.

The session the terminal already owns is the whole transport, so the far end needs a POSIX shell,
base64 and tar, and nothing installed.
Wire format and the reasons behind its parts: docs/protocol.md.
"""

import base64
import io
import shlex
import tarfile

# base64 line length.
# Lines reaching the 4096-byte canonical tty buffer lose data.
WRAP = 76

# Seconds a probe waits before the destination counts as unable to receive.
# A link with hundreds of milliseconds of latency needs more, and the home-manager module rewrites this line.
TIMEOUT = 3

# stty -echo stops the remote tty echoing the payload back at double the traffic.
# tar ends on the first member it cannot write, so cat swallows the rest of the stream.
# A payload that reaches the prompt runs as commands.
# c3RvcA==, ZG9uZQ== and ZmFpbA== decode to stop, done and fail.
RECEIVE = (
    " stty -echo; base64 -d | (tar -xf -; e=$?;"
    " [ $e = 0 ] || printf '\\033]1337;SetUserVar=kdrop=c3RvcA==\\a';"
    " cat >/dev/null; exit $e); s=$?; stty echo;"
    " [ $s = 0 ] && printf '\\033]1337;SetUserVar=kdrop=ZG9uZQ==\\a'"
    " || printf '\\033]1337;SetUserVar=kdrop=ZmFpbA==\\a'\n"
)

# Payload lines per burst, and seconds between bursts.
# A terminal's write queue cannot be recalled, so a burst is what a stopped transfer still costs.
# 5000 lines every 32 ms is around 12 MB/s, above what ssh carries.
BURST = 5000
STEP = 0.032

# Answers the shell lines send back, apart from a probe's free names.
ANSWERS = ('stop', 'done', 'fail')

SILENT = 'No answer from the remote, so the paths were pasted'
UNPACK = 'The remote could not unpack the files'


def rank(destination):
    return 1 if destination.kind == 'session' else None


def probe(wanted):
    """Shell line proving the far end can receive, and naming a free slot per dropped name.

    The reply is ok followed by the free names in order, joined by /.
    A basename holds no /, so the join is unambiguous.
    A leading space hides the line from shells that ignore space-prefixed commands.
    """
    quoted = ' '.join(shlex.quote(n) for n in wanted)
    return (
        " command -v base64 >/dev/null && command -v tar >/dev/null && { r=ok;"
        f' for n in {quoted}; do'
        ' case $n in ?*.*) b=${n%.*}; e=.${n##*.};; *) b=$n; e=;; esac;'
        ' t=$n; i=1; while [ -e "$t" ]; do t=$b-$i$e; i=$((i+1)); done; r=$r/$t;'
        ' done;'
        " printf '\\033]1337;SetUserVar=kdrop=%s\\a' \"$(printf %s \"$r\" | base64 | tr -d '\\n')\"; }\n"
    )


def tarball(plan):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for path, name in plan:
            tar.add(path, arcname=name)
    return buf.getvalue()


class Probe:
    """A probe on the wire, until the far end answers or the timeout runs out."""

    def __init__(self, terminal, wanted, ready, decline):
        self.terminal = terminal
        self.ready = ready
        self.decline = decline
        self.answered = False
        terminal.progress(3)
        terminal.write(probe(wanted))
        terminal.after(TIMEOUT, self.expire)

    def reply(self, value):
        if value in ANSWERS or self.answered:
            return
        self.answered = True
        self.terminal.progress(0)
        self.ready(value.split('/')[1:])

    def expire(self):
        if self.answered:
            return
        self.answered = True
        self.terminal.progress(0)
        self.decline(SILENT)


class Stream:
    """A payload going out burst by burst, until the far end reports what it made of it."""

    def __init__(self, terminal, plan, done, failed):
        self.terminal = terminal
        self.done = done
        self.failed = failed
        terminal.progress(3)
        terminal.write(RECEIVE)
        text = base64.b64encode(tarball(plan))
        self.lines = [text[i:i + WRAP] for i in range(0, len(text), WRAP)]
        self.pump()

    def pump(self):
        """Type the next burst, and arm the one after it until the payload runs out."""
        if self.lines is None:
            return
        if not self.terminal.alive:
            self.lines = None
            return
        burst, self.lines = self.lines[:BURST], self.lines[BURST:]
        if self.lines:
            self.terminal.write(b'\n'.join(burst) + b'\n')
            self.terminal.after(STEP, self.pump)
            return
        self.lines = None
        # 0x04 at line start ends base64's stdin.
        self.terminal.write(b'\n'.join(burst) + b'\n\x04')

    def cut(self):
        """Drop what is left of a payload the far end has stopped reading, and end the stream.

        A burst is whole lines, so the truncated base64 still decodes and the 0x04 lands at a line start.
        Only ever sent while the far end is draining, since a 0x04 at a prompt ends the session.
        """
        if self.lines is not None:
            self.lines = None
            self.terminal.write(b'\x04')

    def reply(self, value):
        if value == 'stop':
            self.cut()
        elif value == 'done':
            self.terminal.progress(0)
            self.done()
        elif value == 'fail':
            self.terminal.progress(2)
            self.failed(UNPACK)


def resolve(destination, wanted, ready, decline):
    return Probe(destination.terminal, wanted, ready, decline)


def send(destination, plan, done, failed):
    return Stream(destination.terminal, plan, done, failed)
