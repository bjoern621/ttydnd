#!/usr/bin/env python3
"""Drive ttydnd against real shells on real ptys.

Python and the destination shell resolve free names independently, so the suite runs both and
compares.  A mismatch would make the confirmation dialog lie.
The terminal port is faked, so every domain but the kitty backend runs without kitty.
"""

import base64
import hashlib
import os
import pty
import re
import select
import shutil
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACK = re.compile(rb'\x1b\]1337;SetUserVar=kdrop=([A-Za-z0-9+/=]*)\x07')

failures = []


def check(name, got, want):
    if got == want:
        print(f'ok   {name}')
    else:
        print(f'FAIL {name}\n       got  {got!r}\n       want {want!r}')
        failures.append(name)


def load():
    """Load the kitty backend with kitty's modules stubbed out, and the package it bootstraps."""
    for name, attrs in (
        ('kitty', {}),
        ('kitty.utils', {'parse_uri_list': lambda text: []}),
        ('kitty.window', {'Window': type('Window', (), {'on_drop': lambda self, drop: None})}),
        ('kitty.boss', {'get_boss': lambda: None}),
        ('kitty.fast_data_types', {'add_timer': lambda *a: None, 'remove_timer': lambda *a: None}),
    ):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
    path = os.path.join(ROOT, 'kitty', 'drop.py')
    module = types.ModuleType('drop')
    module.__file__ = path
    exec(compile(open(path).read(), path, 'exec'), module.__dict__)
    return module


class Terminal:
    """A terminal port that records what the domains ask of it.

    A zero delay runs at once, the way a backend's deferral reaches the next tick.
    """

    def __init__(self, window_id=1):
        self.id = window_id
        self.alive = True
        self.written = []
        self.timers = []
        self.dialogs = []
        self.answers = []
        self.told = []
        self.states = []
        self.pasted = 0

    def write(self, data):
        self.written.append(data)

    def after(self, seconds, run):
        if seconds:
            self.timers.append(run)
        else:
            run()

    def progress(self, state):
        self.states.append(state)

    def fallback(self):
        self.pasted += 1

    def tell(self, text):
        self.told.append(text)

    def ask(self, message, choices, default, answered):
        self.dialogs.append((message, choices, default))
        self.answers.append(answered)

    def answer(self, value):
        self.answers.pop(0)(value)

    def tick(self):
        while self.timers:
            self.timers.pop(0)()


class Window:
    """A kitty window, down to what the backend touches."""

    def __init__(self, window_id=7):
        self.id = window_id
        self.states = []
        self.written = []
        self.pastes = 0
        self.progress = types.SimpleNamespace(
            update=self.states.append, state=types.SimpleNamespace(value=0), percent=0)
        self.screen = types.SimpleNamespace(
            set_progress=lambda *a: None, is_using_alternate_linebuf=lambda: False)

    def write_to_child(self, data):
        self.written.append(data)

    def tabref(self):
        return None

    def original_on_drop(self, drop):
        self.pastes += 1
        return 'pasted'


class Shell:
    """A shell on a pty, read continuously so the child never blocks on a full buffer."""

    def __init__(self, argv, cwd, env=None):
        self.output = bytearray()
        self.stop = False
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
            if env is not None:
                os.environ.clear()
                os.environ.update(env)
            os.environ['PS1'] = '$ '
            os.execvp(argv[0], argv)
        threading.Thread(target=self._read, daemon=True).start()
        time.sleep(0.5)
        self.output.clear()

    def _read(self):
        while not self.stop:
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    self.output.extend(os.read(self.fd, 65536))
                except OSError:
                    return

    def send(self, data):
        view = memoryview(data)
        sent = 0
        while sent < len(view):
            sent += os.write(self.fd, view[sent:sent + 2048])

    def acks(self, wait):
        time.sleep(wait)
        return [base64.b64decode(v).decode() for v in ACK.findall(bytes(self.output))]

    def ack(self, wait):
        seen = self.acks(wait)
        return seen[-1] if seen else None

    def close(self):
        self.stop = True
        try:
            os.close(self.fd)
        except OSError:
            pass


def await_var(shell, value, timeout=2.0):
    """Block until the shell sends this user var back, or the timeout lapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if value in shell.acks(0):
            return True
        time.sleep(0.01)
    return False


SHELLS = [s for s in ('sh', 'bash', 'zsh', 'dash', 'ash') if shutil.which(s)]


def test_names(pkg, tmp):
    names, confirm, local, tty = pkg.names, pkg.confirm, pkg.copy.local, pkg.copy.tty
    check('split', [names.split(n) for n in ('notes.txt', '.bashrc', 'data', 'a.b.c')],
          [('notes', '.txt'), ('.bashrc', ''), ('data', ''), ('a.b', '.c')])
    check('plan_names dedups a drop',
          confirm.plan_names(['/a/x.txt', '/b/x.txt', '/c/x.txt', '/d/y']),
          ['x.txt', 'x-1.txt', 'x-2.txt', 'y'])

    cwd = os.path.join(tmp, 'names')
    os.makedirs(cwd)
    for name in ('notes.txt', 'notes-1.txt', '.bashrc', 'a.b.c', 'my file.txt', "od'd"):
        open(os.path.join(cwd, name), 'w').write('x')
    os.makedirs(os.path.join(cwd, 'data'))
    wanted = ['notes.txt', 'data', '.bashrc', 'a.b.c', 'my file.txt', "od'd", 'free.txt']
    want = ['notes-2.txt', 'data-1', '.bashrc-1', 'a.b-1.c', 'my file-1.txt', "od'd-1", 'free.txt']
    check('free_names', local.free_names(cwd, wanted), want)

    for name in SHELLS:
        shell = Shell([name], cwd)
        shell.send(tty.probe(wanted).encode())
        reply = shell.ack(1.0)
        shell.close()
        got = reply.split('/')[1:] if reply else None
        check(f'probe resolves the same names under {name}', got, want)


def test_switcher(pkg, tmp):
    """A destination reaches the highest ranked transport that serves it, and nothing else."""
    copy, tty, local = pkg.copy, pkg.copy.tty, pkg.copy.local
    terminal = Terminal(11)
    session = pkg.Destination(terminal, 'session')
    directory = pkg.Destination(terminal, 'directory', tmp)
    check('a session goes to the tty transport', copy.choose(session), tty)
    check('a directory goes to the local transport', copy.choose(directory), local)

    scp = types.SimpleNamespace(rank=lambda destination: 2 if destination.kind == 'session' else None)
    copy.TRANSPORTS.insert(0, scp)
    try:
        check('the higher rank wins', copy.choose(session), scp)
        check('a transport that declines the kind is passed over', copy.choose(directory), local)
    finally:
        copy.TRANSPORTS.remove(scp)

    kept = []
    copy.resolve(directory, ['free.txt'], kept.append, kept.append)
    check('a local answer needs no round trip', kept, [['free.txt']])
    check('a transport that finished holds nothing', copy.active(terminal.id), False)

    copy.resolve(session, ['free.txt'], kept.append, kept.append)
    check('a probe holds the terminal until it is answered', copy.active(terminal.id), True)
    copy.reply(terminal.id, 'ok/free-1.txt')
    check('the answer names the free slot', kept[-1], ['free-1.txt'])
    check('an answered probe lets go', copy.active(terminal.id), False)

    copy.resolve(session, ['free.txt'], kept.append, kept.append)
    terminal.tick()
    check('a silent destination declines', kept[-1], tty.SILENT)
    check('a declined probe lets go', copy.active(terminal.id), False)

    nowhere = pkg.Destination(terminal, 'carrier pigeon')
    copy.resolve(nowhere, ['free.txt'], kept.append, kept.append)
    check('a destination no transport serves declines', kept[-1], copy.UNREACHABLE)


def test_transfer(pkg, tmp):
    tty = pkg.copy.tty
    source = os.path.join(tmp, 'src')
    os.makedirs(os.path.join(source, 'dir', 'sub'))
    open(os.path.join(source, 'plain.bin'), 'wb').write(os.urandom(1_000_000))
    open(os.path.join(source, 'dir', 'sub', 'note.txt'), 'w').write('hällö\n')
    os.chmod(os.path.join(source, 'plain.bin'), 0o640)
    paths = [os.path.join(source, 'plain.bin'), os.path.join(source, 'dir')]

    dest = os.path.join(tmp, 'dest')
    os.makedirs(dest)
    open(os.path.join(dest, 'plain.bin'), 'w').write('ORIGINAL')

    wanted = pkg.confirm.plan_names(paths)
    free = pkg.copy.local.free_names(dest, wanted)
    check('collision resolved before sending', free, ['plain-1.bin', 'dir'])

    shell = Shell(['sh'], dest)
    shell.send(tty.RECEIVE.encode())
    check('the shell signals rdy before the payload', await_var(shell, 'rdy'), True)
    shell.output.clear()
    payload = base64.b64encode(tty.tarball(list(zip(paths, free))))
    lines = [payload[i:i + tty.WRAP] for i in range(0, len(payload), tty.WRAP)]
    shell.send(b'\n'.join(lines) + b'\n\x04')
    reply = shell.ack(2.0)
    shell.close()

    def digest(path):
        return hashlib.sha256(open(path, 'rb').read()).hexdigest()

    check('transfer acks done', reply, 'done')
    check('existing name untouched', open(os.path.join(dest, 'plain.bin')).read(), 'ORIGINAL')
    check('renamed copy is byte identical',
          digest(os.path.join(dest, 'plain-1.bin')), digest(os.path.join(source, 'plain.bin')))
    check('mode survives', oct(os.stat(os.path.join(dest, 'plain-1.bin')).st_mode & 0o777), '0o640')
    check('nested file survives',
          open(os.path.join(dest, 'dir', 'sub', 'note.txt')).read(), 'hällö\n')
    # stty -echo held for the whole payload, so only the prompt comes back.
    visible = re.sub(rb'\x1b\][^\x07]*\x07', b'', bytes(shell.output))
    check('echo stays suppressed', len(visible) < 64, True)


def test_local(pkg, tmp):
    """The local transport writes the plan it is handed, and reports the name it could not write."""
    source = os.path.join(tmp, 'local-src')
    os.makedirs(os.path.join(source, 'tree'))
    open(os.path.join(source, 'a.txt'), 'w').write('new')
    open(os.path.join(source, 'tree', 'deep.txt'), 'w').write('deep')
    dest = os.path.join(tmp, 'local-dest')
    os.makedirs(dest)
    open(os.path.join(dest, 'a.txt'), 'w').write('old')

    terminal = Terminal(3)
    destination = pkg.Destination(terminal, 'directory', dest)
    seen = []
    pkg.copy.send(destination, [(os.path.join(source, 'a.txt'), 'a-1.txt'),
                                (os.path.join(source, 'tree'), 'tree')],
                  lambda: seen.append('done'), seen.append)
    check('the plan lands under the names it carries',
          (open(os.path.join(dest, 'a.txt')).read(), open(os.path.join(dest, 'a-1.txt')).read()),
          ('old', 'new'))
    check('a directory lands whole', open(os.path.join(dest, 'tree', 'deep.txt')).read(), 'deep')
    check('a finished copy clears the marker', (seen, terminal.states), (['done'], [0]))

    terminal = Terminal(4)
    missing = pkg.Destination(terminal, 'directory', os.path.join(tmp, 'nowhere'))
    seen = []
    pkg.copy.send(missing, [(os.path.join(source, 'a.txt'), 'a.txt')],
                  lambda: seen.append('done'), seen.append)
    check('a copy that cannot land names the item', seen[0].startswith('Could not copy a.txt.'), True)
    check('a failed copy marks the window', terminal.states, [2])


def test_choices(pkg, tmp):
    """Each item gets its own dialog, and nothing is written before the last answer."""
    source = os.path.join(tmp, 'choice-src')
    os.makedirs(source)
    for name in ('a.txt', 'b.txt'):
        open(os.path.join(source, name), 'w').write('x')
    dest = os.path.join(tmp, 'choice-dest')
    os.makedirs(dest)
    open(os.path.join(dest, 'a.txt'), 'w').write('old')

    paths = [os.path.join(source, n) for n in ('a.txt', 'b.txt')]
    wanted = pkg.confirm.plan_names(paths)
    free = pkg.copy.local.free_names(dest, wanted)
    sent = []
    send = pkg.copy.send
    pkg.copy.send = lambda destination, plan, done, failed: sent.append(plan)
    seen = {}

    def run_with(answers, items=slice(None)):
        sent.clear()
        terminal = Terminal(5)
        destination = pkg.Destination(terminal, 'directory', dest)
        pkg.confirm.ask(destination, paths[items], wanted[items], free[items])
        # Answers arrive one per dialog, the way a backend delivers them after each overlay closes.
        for answer in answers:
            terminal.answer(answer)
        seen['dialogs'] = terminal.dialogs
        seen['told'] = terminal.told
        return [([os.path.basename(p) for p, _ in plan], [n for _, n in plan]) for plan in sent]

    try:
        check('keep both then copy writes the spare name', run_with(['k', 'y']),
              [(['a.txt', 'b.txt'], ['a-1.txt', 'b.txt'])])
        check('clash dialog names the item and the spare',
              seen['dialogs'][0],
              (f'a.txt (1 kB) already exists in\n{dest}.\nKeep both writes a-1.txt.\n\n'
               'Item 1 of 2. Esc cancels the drop.',
               ('k;green:Keep both', 'o;red:Overwrite', 's;yellow:Skip'), 'k'))
        check('free dialog asks copy or skip',
              seen['dialogs'][1],
              (f'Copy b.txt (1 kB) into\n{dest}?\n\nItem 2 of 2. Esc cancels the drop.',
               ('y;green:Copy', 's;yellow:Skip'), 'y'))
        check('overwrite writes the dropped name', run_with(['o', 'y']),
              [(['a.txt', 'b.txt'], ['a.txt', 'b.txt'])])
        check('skip leaves that item alone', run_with(['s', 'y']), [(['b.txt'], ['b.txt'])])
        check('skipping everything writes nothing', run_with(['s', 's']), [])
        check('skipping everything stays quiet', seen['told'], [])
        check('esc after an answer writes nothing', run_with(['k', '']), [])
        check('esc on the first item stops there', (run_with(['']), len(seen['dialogs'])), ([], 1))
        check('a lone item carries no count', run_with(['y'], slice(1, 2)) and seen['dialogs'][0][0],
              f'Copy b.txt (1 kB) into\n{dest}?\n\nEsc cancels the drop.')

        terminal = Terminal(6)
        session = pkg.Destination(terminal, 'session')
        pkg.confirm.ask(session, paths[:1], wanted[:1], ['a-1.txt'])
        check('a session names the working directory it cannot see',
              terminal.dialogs[0][0].splitlines()[1], 'the remote working directory.')
    finally:
        pkg.copy.send = send


def test_silence(pkg, tmp):
    """A destination that never answers pastes the paths and says so."""
    source = os.path.join(tmp, 'silence')
    os.makedirs(source)
    open(os.path.join(source, 'a.txt'), 'w').write('x')
    terminal = Terminal(8)
    pkg.confirm.start(pkg.Destination(terminal, 'session'), [os.path.join(source, 'a.txt')])
    check('the probe goes out first', terminal.written[0].startswith(' command -v base64'), True)
    check('nothing is asked while the probe is out', terminal.dialogs, [])
    terminal.tick()
    check('the timeout pastes the paths and reports', (terminal.pasted, terminal.told),
          (1, [pkg.copy.tty.SILENT]))


def test_reports(drop):
    """A failure stays until closed, a newer one replaces it, and a drop onto it goes through."""
    timers = []
    shown = []
    closed = []

    class Boss:
        window_id_map = {}

        def choose(self, message, callback, *choices, **kw):
            shown.append((message, callback, choices, kw['default']))
            return types.SimpleNamespace(id=100 + len(shown))

        def mark_window_for_close(self, window_id):
            closed.append(window_id)

    drop.add_timer = lambda fn, delay, repeat: timers.append(fn) or len(timers)
    boss = Boss()
    window = Window()
    boss.window_id_map[7] = window
    drop.get_boss = lambda: boss

    drop.report(7, 'Could not copy a.txt')
    timers[-1](1)
    check('a failure shows as an overlay with one button',
          (shown[0][0], shown[0][2], shown[0][3]), ('Could not copy a.txt', ('o:OK',), 'o'))
    check('nothing closes it on its own', len(timers), 1)
    drop.report(7, 'Could not copy b.txt')
    check('second report closes the first overlay', closed, [101])
    timers[-1](2)
    shown[0][1]('')
    check('stale overlay closing leaves the new one', (drop.reports[7]['overlay'], closed), (102, [101]))
    drop.ttydnd.copy.inflight[7] = types.SimpleNamespace(reply=lambda value: None)
    marks = len(window.states)
    shown[1][1]('o')
    check('enter closes the overlay', (drop.reports, closed), ({}, [101, 102]))
    check('a transport holding the window keeps its marker', window.states[marks:], [])
    del drop.ttydnd.copy.inflight[7]

    drop.report(7, 'Could not copy c.txt')
    drop.report(7, 'Could not copy d.txt')
    timers[-2](3)
    timers[-1](4)
    check('a report replaced before showing never opens', [s[0] for s in shown[2:]], ['Could not copy d.txt'])
    check('the failure overlay is known as a dialog', drop.dialogs, {103: 7})

    marks = len(window.states)
    check('a drop onto the failure lands on the window beneath',
          drop.on_drop(Window(103), {}), 'pasted')
    check('the failure made way', (drop.reports, drop.dialogs, closed[-1]), ({}, {}, 103))
    check('a free window clears its marker', window.states[marks:], [0])
    drop.dialogs[104] = 7
    check('a drop onto an open decision waits', drop.on_drop(Window(104), {}), None)
    drop.dialogs.clear()


def test_backend(drop, tmp):
    """What kitty hands over reaches a domain, or kitty's own handling, per window."""
    source = os.path.join(tmp, 'backend')
    os.makedirs(source)
    path = os.path.join(source, 'a.txt')
    open(path, 'w').write('x')

    timers = []
    shown = []

    class Boss:
        window_id_map = {}

        def choose(self, message, callback, *choices, **kw):
            shown.append(message)
            return types.SimpleNamespace(id=200 + len(shown))

        def mark_window_for_close(self, window_id):
            pass

    drop.add_timer = lambda fn, delay, repeat: timers.append(fn) or len(timers)
    drop.parse_uri_list = lambda text: [path]
    boss = Boss()
    drop.get_boss = lambda: boss

    window = Window(20)
    window.child_is_remote = False
    window.at_prompt = True
    window.cwd_for_serialization = tmp
    boss.window_id_map[20] = window
    drop.on_drop(window, {})
    while timers:
        timers.pop(0)(0)
    check('a shell at a prompt is asked about the item',
          shown, [f'Copy a.txt (1 kB) into\n{tmp}?\n\nEsc cancels the drop.'])

    window.at_prompt = False
    check('a shell running something keeps kitty handling', drop.on_drop(window, {}), 'pasted')
    window.at_prompt = True
    window.screen.is_using_alternate_linebuf = lambda: True
    check('a full-screen program keeps kitty handling', drop.on_drop(window, {}), 'pasted')
    window.screen.is_using_alternate_linebuf = lambda: False

    window.child_is_remote = True
    drop.on_drop(window, {})
    check('a remote shell is probed before anything is asked',
          (window.written[0].startswith(' command -v base64'), len(shown)), (True, 1))
    pastes = window.pastes
    while timers:
        timers.pop(0)(0)
    check('a silent remote pastes the paths and reports',
          (window.pastes - pastes, shown[-1]), (1, drop.ttydnd.copy.tty.SILENT))
    drop.ttydnd.copy.inflight.pop(20, None)


def test_stream(pkg, tmp):
    """The payload goes out in bursts, and a destination that stops reading ends the stream early."""
    tty = pkg.copy.tty
    source = os.path.join(tmp, 'stream')
    os.makedirs(source)
    open(os.path.join(source, 'a.txt'), 'wb').write(os.urandom(4000))
    plan = [(os.path.join(source, 'a.txt'), 'a.txt')]

    terminal = Terminal(9)
    destination = pkg.Destination(terminal, 'session')
    burst = tty.BURST
    tty.BURST = 2

    def typed():
        return b''.join(terminal.written[1:]).replace(b'\n', b'')

    try:
        pkg.copy.send(destination, plan, lambda: None, terminal.tell)
        check('the receive line goes first', terminal.written[0], tty.RECEIVE)
        check('the payload waits for rdy', len(terminal.written), 1)
        pkg.copy.reply(9, 'rdy')
        check('a burst carries whole lines', terminal.written[1].count(b'\n'), 2)
        written = len(terminal.written)
        pkg.copy.reply(9, 'rdy')
        check('a second rdy starts no second stream', len(terminal.written), written)
        check('a burst at a time', len(terminal.timers), 1)
        while terminal.timers:
            terminal.timers.pop(0)()
        check('the last burst ends the stream', terminal.written[-1][-2:], b'\n\x04')
        check('the bursts carry the whole archive',
              base64.b64decode(typed().rstrip(b'\x04')), tty.tarball(plan))
        check('a finished transfer still holds the terminal', pkg.copy.active(9), True)
        pkg.copy.reply(9, 'done')
        check('done clears the marker and lets go',
              (terminal.states[-1], pkg.copy.active(9)), (0, False))

        terminal = Terminal(9)
        destination = pkg.Destination(terminal, 'session')
        pkg.copy.send(destination, plan, lambda: None, terminal.tell)
        pkg.copy.reply(9, 'rdy')
        pkg.copy.reply(9, 'stop')
        check('a cut stream ends at a line start', terminal.written[-1], b'\x04')
        check('a cut stream stays whole lines', len(typed().rstrip(b'\x04')) % tty.WRAP, 0)
        terminal.timers.pop(0)()
        check('a cut stream types nothing more', terminal.written[-1], b'\x04')
        pkg.copy.reply(9, 'stop')
        check('cutting a finished stream types nothing', terminal.written[-1], b'\x04')
        pkg.copy.reply(9, 'fail')
        check('fail marks the window and reports',
              (terminal.states[-1], terminal.told), (2, [tty.UNPACK]))
        check('a failed transfer lets go', pkg.copy.active(9), False)
    finally:
        tty.BURST = burst
        pkg.copy.inflight.pop(9, None)


def test_refusals(pkg, tmp):
    tty = pkg.copy.tty
    for name in SHELLS:
        dest = os.path.join(tmp, f'garbage-{name}')
        os.makedirs(dest)
        shell = Shell([name], dest)
        shell.send(tty.RECEIVE.encode())
        await_var(shell, 'rdy')
        shell.output.clear()
        payload = base64.b64encode(os.urandom(20000))
        lines = [payload[i:i + tty.WRAP] for i in range(0, len(payload), tty.WRAP)]
        shell.send(b'\n'.join(lines) + b'\n\x04')
        seen = shell.acks(1.5)
        output = bytes(shell.output)
        shell.close()
        check(f'a stream tar cannot read stops then acks fail under {name}', seen, ['stop', 'fail'])
        # tar quits on the first record, and the rest of the payload would run as commands.
        check(f'a refused stream stays off the prompt under {name}',
              [line for line in lines[-4:] if line in output], [])

    empty = os.path.join(tmp, 'nobin')
    os.makedirs(empty)
    shell = Shell(['/bin/sh'], empty, env={'PATH': empty})
    shell.send(tty.probe(['x']).encode())
    reply = shell.ack(1.0)
    shell.close()
    check('probe stays silent without base64', reply, None)


def test_delivery(pkg, tmp):
    """A payload delivered after rdy lands whole under every shell, none of it on the prompt.

    An interactive zsh reads the prompt in blocks, so a payload sent before rdy lands in its line
    editor and the archive arrives truncated.  The rdy handshake holds the payload until base64 reads.
    """
    tty = pkg.copy.tty
    for name in SHELLS:
        source = os.path.join(tmp, f'deliver-src-{name}')
        dest = os.path.join(tmp, f'deliver-dest-{name}')
        os.makedirs(source)
        os.makedirs(dest)
        data = os.urandom(50000)
        open(os.path.join(source, 'blob.bin'), 'wb').write(data)

        shell = Shell([name], dest)
        shell.send(tty.RECEIVE.encode())
        rdy = await_var(shell, 'rdy')
        shell.output.clear()
        payload = base64.b64encode(tty.tarball([(os.path.join(source, 'blob.bin'), 'blob.bin')]))
        lines = [payload[i:i + tty.WRAP] for i in range(0, len(payload), tty.WRAP)]
        shell.send(b'\n'.join(lines) + b'\n\x04')
        reply = shell.ack(2.0)
        landed = os.path.join(dest, 'blob.bin')
        whole = os.path.exists(landed) and open(landed, 'rb').read() == data
        visible = re.sub(rb'\x1b\][^\x07]*\x07', b'', bytes(shell.output))
        shell.close()
        check(f'rdy precedes the payload under {name}', rdy, True)
        check(f'the payload lands whole under {name}', (reply, whole), ('done', True))
        check(f'no payload line runs as a command under {name}', b'not found' in visible, False)


def main():
    drop = load()
    pkg = drop.ttydnd
    print(f'shells under test: {", ".join(SHELLS)}\n')
    with tempfile.TemporaryDirectory() as tmp:
        test_names(pkg, tmp)
        test_switcher(pkg, tmp)
        test_transfer(pkg, tmp)
        test_local(pkg, tmp)
        test_choices(pkg, tmp)
        test_silence(pkg, tmp)
        test_reports(load())
        test_backend(load(), tmp)
        test_stream(pkg, tmp)
        test_delivery(pkg, tmp)
        test_refusals(pkg, tmp)
    print()
    if failures:
        print(f'{len(failures)} failed: {", ".join(failures)}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
