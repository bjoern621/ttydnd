#!/usr/bin/env python3
"""Drive the shell half of ttydnd against a real pty.

Python and the remote shell resolve free names independently, so the suite runs
both and compares.  A mismatch would make the confirmation dialog lie.
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
    """Import the watcher with kitty's modules stubbed out."""
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
    exec(compile(open(path).read(), path, 'exec'), module.__dict__)
    return module


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

    def ack(self, wait):
        time.sleep(wait)
        match = ACK.search(bytes(self.output))
        return base64.b64decode(match.group(1)).decode() if match else None

    def close(self):
        self.stop = True
        try:
            os.close(self.fd)
        except OSError:
            pass


SHELLS = [s for s in ('sh', 'bash', 'zsh', 'dash', 'ash') if shutil.which(s)]


def test_names(drop, tmp):
    check('split_name', [drop.split_name(n) for n in ('notes.txt', '.bashrc', 'data', 'a.b.c')],
          [('notes', '.txt'), ('.bashrc', ''), ('data', ''), ('a.b', '.c')])
    check('plan_names dedups a drop',
          drop.plan_names(['/a/x.txt', '/b/x.txt', '/c/x.txt', '/d/y']),
          ['x.txt', 'x-1.txt', 'x-2.txt', 'y'])

    cwd = os.path.join(tmp, 'names')
    os.makedirs(cwd)
    for name in ('notes.txt', 'notes-1.txt', '.bashrc', 'a.b.c', 'my file.txt', "od'd"):
        open(os.path.join(cwd, name), 'w').write('x')
    os.makedirs(os.path.join(cwd, 'data'))
    names = ['notes.txt', 'data', '.bashrc', 'a.b.c', 'my file.txt', "od'd", 'free.txt']
    want = ['notes-2.txt', 'data-1', '.bashrc-1', 'a.b-1.c', 'my file-1.txt', "od'd-1", 'free.txt']
    check('resolve_local', drop.resolve_local(cwd, names), want)

    for name in SHELLS:
        shell = Shell([name], cwd)
        shell.send(drop.probe(names).encode())
        reply = shell.ack(1.0)
        shell.close()
        got = reply.split('/')[1:] if reply else None
        check(f'probe resolves the same names under {name}', got, want)


def test_transfer(drop, tmp):
    source = os.path.join(tmp, 'src')
    os.makedirs(os.path.join(source, 'dir', 'sub'))
    open(os.path.join(source, 'plain.bin'), 'wb').write(os.urandom(1_000_000))
    open(os.path.join(source, 'dir', 'sub', 'note.txt'), 'w').write('hällö\n')
    os.chmod(os.path.join(source, 'plain.bin'), 0o640)
    paths = [os.path.join(source, 'plain.bin'), os.path.join(source, 'dir')]

    dest = os.path.join(tmp, 'dest')
    os.makedirs(dest)
    open(os.path.join(dest, 'plain.bin'), 'w').write('ORIGINAL')

    names = drop.plan_names(paths)
    free = drop.resolve_local(dest, names)
    check('collision resolved before sending', free, ['plain-1.bin', 'dir'])

    shell = Shell(['sh'], dest)
    shell.send(drop.RECEIVE.encode())
    time.sleep(0.4)
    shell.output.clear()
    payload = base64.b64encode(drop.tarball(paths, free))
    lines = [payload[i:i + drop.WRAP] for i in range(0, len(payload), drop.WRAP)]
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


def test_choices(drop, tmp):
    """Each button writes a different set of names, so pin all three."""
    source = os.path.join(tmp, 'choice-src')
    os.makedirs(source)
    for name in ('a.txt', 'b.txt'):
        open(os.path.join(source, name), 'w').write('x')
    dest = os.path.join(tmp, 'choice-dest')
    os.makedirs(dest)
    open(os.path.join(dest, 'a.txt'), 'w').write('old')

    paths = [os.path.join(source, n) for n in ('a.txt', 'b.txt')]
    names = drop.plan_names(paths)
    free = drop.resolve_local(dest, names)
    seen = {}

    class Boss:
        def __init__(self, answer):
            self.answer = answer

        def choose(self, message, callback, *choices, **kw):
            seen['message'] = message
            seen['choices'] = choices
            seen['default'] = kw['default']
            callback(self.answer)

    drop.add_timer = lambda fn, delay, repeat: fn(0)
    drop.report = lambda window, text: seen.__setitem__('reported', text)

    def run_with(answer):
        written = []
        drop.get_boss = lambda: Boss(answer)
        seen.pop('reported', None)
        drop.ask(None, paths, names, free, f'into {dest}',
                 lambda p, n: written.append(([os.path.basename(x) for x in p], n)))
        return written

    check('keep both writes the free names', run_with('k'),
          [(['a.txt', 'b.txt'], ['a-1.txt', 'b.txt'])])
    check('overwrite writes the dropped names', run_with('o'),
          [(['a.txt', 'b.txt'], ['a.txt', 'b.txt'])])
    check('skip writes only what was free', run_with('s'), [(['b.txt'], ['b.txt'])])
    check('esc writes nothing', run_with(''), [])
    check('esc reports nothing copied', seen['reported'], 'Nothing copied')
    check('clash choices', seen['choices'],
          ('k;green:Keep both', 'o;red:Overwrite', 's;yellow:Skip'))
    check('keep both is the default', seen['default'], 'k')
    check('message names the clash', 'a.txt already exists.' in seen['message'], True)
    check('message offers a way out', 'Esc cancels.' in seen['message'], True)

    # A drop with nothing to resolve keeps the plain pair.
    drop.get_boss = lambda: Boss('y')
    written = []
    drop.ask(None, paths, names, names, f'into {dest}',
             lambda p, n: written.append(n))
    check('clean drop choices', seen['choices'], ('y;green:Copy', 'c;red:Cancel'))
    check('clean drop writes every name', written, [['a.txt', 'b.txt']])


def test_reports(drop):
    """A second result inside the linger keeps the title from before the first."""
    timers = []
    removed = []
    drop.add_timer = lambda fn, delay, repeat: timers.append(fn) or len(timers)
    drop.remove_timer = removed.append
    drop.progress = lambda window, state: None

    class Window:
        id = 7
        override_title = 'mine'

        def set_title(self, title):
            self.override_title = title

    window = Window()
    drop.get_boss = lambda: types.SimpleNamespace(window_id_map={7: window})
    drop.report(window, 'Copied a.txt')
    drop.report(window, 'Copied b.txt')
    check('second report cancels the first timer', removed, [1])
    check('title shows the latest result', window.override_title, 'Copied b.txt')
    timers[-1](2)
    check('restore brings the earlier title back', window.override_title, 'mine')
    check('restore forgets the window', drop.reports, {})


def test_refusals(drop, tmp):
    for name in SHELLS:
        dest = os.path.join(tmp, f'garbage-{name}')
        os.makedirs(dest)
        shell = Shell([name], dest)
        shell.send(drop.RECEIVE.encode())
        time.sleep(0.4)
        shell.output.clear()
        payload = base64.b64encode(os.urandom(20000))
        lines = [payload[i:i + drop.WRAP] for i in range(0, len(payload), drop.WRAP)]
        shell.send(b'\n'.join(lines) + b'\n\x04')
        reply = shell.ack(1.5)
        shell.close()
        check(f'a stream tar cannot read acks fail under {name}', reply, 'fail')

    empty = os.path.join(tmp, 'nobin')
    os.makedirs(empty)
    shell = Shell(['/bin/sh'], empty, env={'PATH': empty})
    shell.send(drop.probe(['x']).encode())
    reply = shell.ack(1.0)
    shell.close()
    check('probe stays silent without base64', reply, None)


def main():
    drop = load()
    print(f'shells under test: {", ".join(SHELLS)}\n')
    with tempfile.TemporaryDirectory() as tmp:
        test_names(drop, tmp)
        test_transfer(drop, tmp)
        test_choices(drop, tmp)
        test_reports(load())
        test_refusals(drop, tmp)
    print()
    if failures:
        print(f'{len(failures)} failed: {", ".join(failures)}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
