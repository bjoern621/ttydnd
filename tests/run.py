#!/usr/bin/env python3
"""Drive the shell half of ttydnd against a real pty.

Python and the remote shell resolve free names independently, so the suite runs
both and compares.  A mismatch would make the confirmation dialog lie.
"""

import base64
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shutil
import struct
import sys
import tempfile
import termios
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
        ('kitty.fast_data_types', {'add_timer': lambda *a: None}),
    ):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
    path = os.path.join(ROOT, 'kitty', 'drop.py')
    module = types.ModuleType('drop')
    # runpy.run_path sets __file__, and the watcher finds confirm.py through it.
    module.__dict__['__file__'] = path
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

        def run_kitten_with_metadata(self, kitten, args, **kw):
            spec = json.loads(args[0])
            seen['message'] = spec['message']
            seen['choices'] = tuple(c['letter'] for c in spec['choices'])
            seen['labels'] = tuple(c['label'] for c in spec['choices'])
            seen['default'] = spec['default']
            kw['custom_callback']({'response': self.answer}, 0, self)
            kw['action_on_removal'](0, self)

    drop.add_timer = lambda fn, delay, repeat: fn(0)
    drop.notify = lambda window, title, body: seen.__setitem__('notified', title)

    def run_with(answer):
        written = []
        drop.get_boss = lambda: Boss(answer)
        seen.pop('notified', None)
        drop.ask(None, paths, names, free, f'into {dest}',
                 lambda p, n: written.append(([os.path.basename(x) for x in p], n)))
        return written

    check('keep both writes the free names', run_with('k'),
          [(['a.txt', 'b.txt'], ['a-1.txt', 'b.txt'])])
    check('overwrite writes the dropped names', run_with('o'),
          [(['a.txt', 'b.txt'], ['a.txt', 'b.txt'])])
    check('skip writes only what was free', run_with('s'), [(['b.txt'], ['b.txt'])])
    check('esc writes nothing', run_with(''), [])
    check('esc notifies', seen['notified'], 'Files not copied')
    check('clash choices', seen['labels'], ('Keep both', 'Overwrite', 'Skip'))
    check('keep both is the default', seen['default'], 'k')
    check('message names the clash', 'a.txt already exists.' in seen['message'], True)
    check('message offers a way out', 'Esc cancels.' in seen['message'], True)

    # A drop with nothing to resolve keeps the plain pair.
    drop.get_boss = lambda: Boss('y')
    written = []
    drop.ask(None, paths, names, names, f'into {dest}',
             lambda p, n: written.append(n))
    check('clean drop choices', seen['labels'], ('Copy', 'Cancel'))
    check('clean drop writes every name', written, [['a.txt', 'b.txt']])


ROWS, COLS, CELL_W, CELL_H = 24, 80, 8, 16
SGR = re.compile(rb'\x1b\[[0-9;]*m')
# The frame of the focused button, painted yellow around its label.
FRAMED = re.compile(rb'\x1b\[33m\xe2\x94\x82\x1b\[39m(.*?)\x1b\[33m\xe2\x94\x82', re.S)
RESULT = re.compile(rb'RESULT=(\{.*?\})')

SPEC = json.dumps({'message': 'Pick one.', 'default': 'k', 'choices': [
    {'letter': 'k', 'color': 'green', 'label': 'Keep both'},
    {'letter': 'o', 'color': 'red', 'label': 'Overwrite'},
    {'letter': 's', 'color': 'yellow', 'label': 'Skip'}]})


def kitty_python_dir():
    exe = shutil.which('kitty')
    if not exe:
        return None
    lib = os.path.join(os.path.dirname(os.path.realpath(exe)), '..', 'lib', 'kitty')
    return os.path.abspath(lib) if os.path.isdir(lib) else None


def at_cell(cell_x, cell_y):
    """SGR pixel coordinates, which is the mode the kitten's mouse tracking asks for."""
    return cell_x * CELL_W + CELL_W // 2, cell_y * CELL_H + CELL_H // 2


def hover(cell_x, cell_y):
    x, y = at_cell(cell_x, cell_y)
    return f'\x1b[<35;{x};{y}M'.encode()


def click(cell_x, cell_y):
    x, y = at_cell(cell_x, cell_y)
    return f'\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m'.encode()


def run_dialog(kitty_dir, keys):
    path = os.path.join(ROOT, 'kitty', 'confirm.py')
    code = (
        'import runpy,sys,json;'
        f'sys.argv=[{path!r}, {SPEC!r}];'
        f'm=runpy.run_path({path!r}, run_name="__run_kitten__");'
        'print("RESULT=" + json.dumps(m["main"](sys.argv)), file=sys.stderr)'
    )
    pid, fd = pty.fork()
    if pid == 0:
        os.environ['PYTHONPATH'] = kitty_dir
        os.environ['TERM'] = 'xterm-256color'
        os.execvp(sys.executable, [sys.executable, '-c', code])
    # SGR pixel mode divides by the cell size, so the pty must report one.
    fcntl.ioctl(fd, termios.TIOCSWINSZ,
                struct.pack('HHHH', ROWS, COLS, COLS * CELL_W, ROWS * CELL_H))

    def drain(seconds):
        out = bytearray()
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out.extend(os.read(fd, 65536))
                except OSError:
                    break
        return out

    out = drain(1.0)
    for key in keys:
        os.write(fd, key)
        time.sleep(0.25)
        out.extend(drain(0.2))
    out.extend(drain(0.6))
    try:
        os.close(fd)
    except OSError:
        pass
    os.waitpid(pid, 0)
    out = bytes(out)
    frames = FRAMED.findall(out)
    answers = RESULT.findall(out)
    return (SGR.sub(b'', frames[-1]).decode().strip() if frames else None,
            json.loads(answers[-1])['response'] if answers else None)


def test_dialog(drop, tmp):
    kitty_dir = kitty_python_dir()
    if not kitty_dir:
        print('skip dialog checks, kitty not on PATH')
        return
    # Enter and Esc in kitty keyboard protocol form, which is what kitty sends.
    cases = (
        ('the default starts framed', [], 'Keep both', None),
        ('right moves the frame', [b'\x1b[C'], 'Overwrite', None),
        ('right twice', [b'\x1b[C', b'\x1b[C'], 'Skip', None),
        ('right wraps', [b'\x1b[C'] * 3, 'Keep both', None),
        ('left wraps', [b'\x1b[D'], 'Skip', None),
        ('tab moves the frame', [b'\t'], 'Overwrite', None),
        ('hover frames the second', [hover(20, 3)], 'Overwrite', None),
        ('hover frames the third', [hover(33, 3)], 'Skip', None),
        ('hover returns to the first', [hover(33, 3), hover(6, 3)], 'Keep both', None),
        ('click answers', [click(33, 3)], None, 's'),
        ('enter takes the framed one', [b'\x1b[C', b'\x1b[13u'], None, 'o'),
        ('a letter answers', [b's'], None, 's'),
        ('esc answers with nothing', [b'\x1b[27u'], None, ''),
    )
    for name, keys, want_frame, want_answer in cases:
        frame, answer = run_dialog(kitty_dir, keys)
        check(name, frame if want_answer is None else answer,
              want_frame if want_answer is None else want_answer)


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
        test_dialog(drop, tmp)
        test_refusals(drop, tmp)
    print()
    if failures:
        print(f'{len(failures)} failed: {", ".join(failures)}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
