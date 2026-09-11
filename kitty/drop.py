# ttydnd kitty backend.
# Drops copy into the shell's working directory, local or over ssh.
# Remote drops type a base64 tar stream into the session, so the remote needs
# only a POSIX shell, base64 and tar.
# Wire format: docs/protocol.md.
# Dragging a name back out is kitty config alone, no code here.

import base64
import io
import os
import shlex
import shutil
import tarfile

from kitty.boss import get_boss
from kitty.fast_data_types import add_timer
from kitty.utils import parse_uri_list
from kitty.window import Window

# base64 line length.
# Lines reaching the 4096-byte canonical tty buffer lose data.
WRAP = 76

# Seconds a probe waits before the drop falls back to kitty's own handling.
TIMEOUT = 3

# stty -echo stops the remote tty echoing the payload back at double the traffic.
# ZG9uZQ== and ZmFpbA== decode to done and fail, arriving as the user var once tar has run.
RECEIVE = (
    " stty -echo; base64 -d | tar -xf -; s=$?; stty echo;"
    " [ $s = 0 ] && printf '\\033]1337;SetUserVar=kdrop=ZG9uZQ==\\a'"
    " || printf '\\033]1337;SetUserVar=kdrop=ZmFpbA==\\a'\n"
)

# Window id to the drop awaiting a probe reply.
pending = {}

# Window ids with a transfer in flight.
sending = set()

# Window id to the failure overlay showing on it.
reports = {}

# Overlay id to the window beneath, for every dialog opened here.
dialogs = {}


def probe(names):
    """Shell line proving the remote can receive, and naming a free slot per dropped name.

    The reply is ok followed by the free names in order, joined by /.
    A basename holds no /, so the join is unambiguous.
    A leading space hides the line from shells that ignore space-prefixed commands.
    """
    quoted = ' '.join(shlex.quote(n) for n in names)
    return (
        " command -v base64 >/dev/null && command -v tar >/dev/null && { r=ok;"
        f' for n in {quoted}; do'
        ' case $n in ?*.*) b=${n%.*}; e=.${n##*.};; *) b=$n; e=;; esac;'
        ' t=$n; i=1; while [ -e "$t" ]; do t=$b-$i$e; i=$((i+1)); done; r=$r/$t;'
        ' done;'
        " printf '\\033]1337;SetUserVar=kdrop=%s\\a' \"$(printf %s \"$r\" | base64 | tr -d '\\n')\"; }\n"
    )


def split_name(name):
    # Mirrors the remote's ?*.* case, so a leading-dot name keeps all of it as the stem.
    stem, dot, ext = name.rpartition('.')
    return (stem, '.' + ext) if dot and stem else (name, '')


def dropped_paths(drop):
    uri_list = drop.get('text/uri-list', b'').decode('utf-8', 'replace')
    return [p for p in parse_uri_list(uri_list) if os.path.exists(p)]


def plan_names(paths):
    """Basename per path, suffixed where two dropped items share one."""
    names = []
    for p in paths:
        name = os.path.basename(p.rstrip('/'))
        stem, ext = split_name(name)
        i = 1
        while name in names:
            name = f'{stem}-{i}{ext}'
            i += 1
        names.append(name)
    return names


def resolve_local(cwd, names):
    """Free name per planned name, in order. The remote's probe answers the same question."""
    free = []
    for name in names:
        stem, ext = split_name(name)
        candidate, i = name, 1
        while os.path.exists(os.path.join(cwd, candidate)) or candidate in free:
            candidate = f'{stem}-{i}{ext}'
            i += 1
        free.append(candidate)
    return free


def total_size(paths):
    total = 0
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
        else:
            total += os.path.getsize(p)
    return total


def size(count):
    return f'{count / 1e6:.1f} MB' if count >= 1e6 else f'{max(1, count // 1000)} kB'


def tarball(paths, names):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for p, name in zip(paths, names):
            tar.add(p, arcname=name)
    return buf.getvalue()


def progress(window, state):
    """Drive the tab bar marker and the progress bar the way an OSC 9;4 report does.

    3 spins, 2 marks an error, 0 clears.
    """
    window.progress.update(state)
    window.screen.set_progress(window.progress.state.value, window.progress.percent)
    tab = window.tabref()
    if tab is not None:
        tab.update_progress()


def report(window, text):
    """Show a failure in an overlay on the window until Enter, Esc or a click closes it."""
    window_id = window.id
    dismiss(window_id)
    entry = {'overlay': None}
    reports[window_id] = entry

    def closed(answer):
        # A newer report owns the window by now, so a stale overlay going away changes nothing.
        if reports.get(window_id) is entry:
            dismiss(window_id)

    # Deferred a tick: every caller runs inside a kitty callback, and the overlay reenters it.
    def show(timer_id):
        if reports.get(window_id) is not entry:
            return
        overlay = get_boss().choose(text, closed, 'o:OK', window=window, default='o', title='Copy files')
        if overlay is None:
            del reports[window_id]
            return
        entry['overlay'] = overlay.id
        dialogs[overlay.id] = window_id

    add_timer(show, 0, False)


def dismiss(window_id):
    entry = reports.pop(window_id, None)
    if entry is None:
        return
    boss = get_boss()
    if entry['overlay'] is not None:
        dialogs.pop(entry['overlay'], None)
        boss.mark_window_for_close(entry['overlay'])
    window = boss.window_id_map.get(window_id)
    # A probe or transfer started meanwhile keeps its spinner.
    if window is not None and window_id not in pending and window_id not in sending:
        progress(window, 0)


def ask(window, paths, names, free, where, run):
    """One dialog per item, then hand run() the paths and the names to write.

    Nothing is written before the last answer, and Esc on any item cancels the drop.
    """
    chosen = []
    total = len(paths)

    def decide(i):
        if i == total:
            if chosen:
                run([p for p, _ in chosen], [n for _, n in chosen])
            return
        path, name, spare = paths[i], names[i], free[i]
        item = f'{name} ({size(total_size([path]))})'
        note = f'Item {i + 1} of {total}. Esc cancels the drop.' if total > 1 else 'Esc cancels the drop.'
        # The destination takes its own line, since a path has no space to wrap at.
        # The ask kitten requires each shortcut letter to occur in its own label.
        if name == spare:
            message = f'Copy {item} into\n{where}?\n\n{note}'
            choices = ('y;green:Copy', 's;yellow:Skip')
            default = 'y'
        else:
            message = f'{item} already exists in\n{where}.\nKeep both writes {spare}.\n\n{note}'
            choices = ('k;green:Keep both', 'o;red:Overwrite', 's;yellow:Skip')
            default = 'k'
        slot = {}

        def answered(answer):
            dialogs.pop(slot.get('overlay'), None)
            if answer in ('y', 'o'):
                chosen.append((path, name))
            elif answer == 'k':
                chosen.append((path, spare))
            elif answer != 's':
                return
            add_timer(lambda timer_id: decide(i + 1), 0, False)

        overlay = get_boss().choose(message, answered, *choices, window=window, default=default, title='Copy files')
        if overlay is not None:
            slot['overlay'] = overlay.id
            dialogs[overlay.id] = window.id

    # Deferred a tick: the callers and every answer run inside a kitty callback, and the overlay reenters it.
    add_timer(lambda timer_id: decide(0), 0, False)


def copy_local(window, cwd, paths, names):
    try:
        for p, name in zip(paths, names):
            dest = os.path.join(cwd, name)
            if os.path.isdir(p):
                shutil.copytree(p, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest)
    except OSError as err:
        progress(window, 2)
        report(window, f'Could not copy {name}. {err.strerror or err}')
        return
    progress(window, 0)


def send_remote(window, paths, names):
    sending.add(window.id)
    progress(window, 3)
    window.write_to_child(RECEIVE)
    text = base64.b64encode(tarball(paths, names))
    lines = [text[i:i + WRAP] for i in range(0, len(text), WRAP)]
    # 0x04 at line start ends base64's stdin.
    window.write_to_child(b'\n'.join(lines) + b'\n\x04')


def expire(window_id):
    entry = pending.pop(window_id, None)
    window = get_boss().window_id_map.get(window_id)
    if entry and window is not None:
        window.original_on_drop(entry['drop'])
        progress(window, 0)
        report(window, 'No answer from the remote, so the paths were pasted')


def on_set_user_var(boss, window, data):
    if data['key'] != 'kdrop':
        return
    if data['value'] in ('done', 'fail'):
        if window.id in sending:
            sending.remove(window.id)
            if data['value'] == 'done':
                progress(window, 0)
            else:
                progress(window, 2)
                report(window, 'The remote could not unpack the files')
        return
    entry = pending.pop(window.id, None)
    if entry is None:
        return
    progress(window, 0)
    ask(window, entry['paths'], entry['names'], data['value'].split('/')[1:],
        'the remote working directory',
        lambda kept, chosen: send_remote(window, kept, chosen))


def on_drop(self, drop):
    base_id = dialogs.get(self.id)
    if base_id is not None:
        # A failure overlay makes way for the drop. A decision still open holds it.
        base = get_boss().window_id_map.get(base_id)
        if base is None or reports.get(base_id, {}).get('overlay') != self.id:
            return None
        dismiss(base_id)
        return on_drop(base, drop)
    paths = dropped_paths(drop)
    # Alternate screen means a full-screen program owns the terminal, local or remote.
    if not paths or self.screen.is_using_alternate_linebuf():
        return self.original_on_drop(drop)
    dismiss(self.id)
    names = plan_names(paths)
    if self.child_is_remote:
        pending[self.id] = {'paths': paths, 'names': names, 'drop': drop}
        progress(self, 3)
        self.write_to_child(probe(names))
        window_id = self.id
        add_timer(lambda timer_id: expire(window_id), TIMEOUT, False)
    elif self.at_prompt:
        cwd = self.cwd_for_serialization
        ask(self, paths, names, resolve_local(cwd, names), cwd,
            lambda kept, chosen: copy_local(self, cwd, kept, chosen))
    else:
        self.original_on_drop(drop)


# A config reload re-runs this file, so keep the first unpatched method.
Window.original_on_drop = getattr(Window, 'original_on_drop', Window.on_drop)
Window.on_drop = on_drop
