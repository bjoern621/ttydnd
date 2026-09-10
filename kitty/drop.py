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

# Window id to the summary of a transfer in flight.
sending = {}


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


def summarize(names):
    if len(names) > 3:
        return ', '.join(names[:3]) + f' and {len(names) - 3} more'
    return ', '.join(names)


def counted(names):
    return '1 item' if len(names) == 1 else f'{len(names)} items'


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


def notify(window, title, body):
    manager = get_boss().notification_manager
    cmd = manager.create_notification_cmd()
    cmd.title = title
    cmd.body = body
    manager.notify_with_command(cmd, window.id)


def ask(window, paths, names, free, destination, run):
    """Confirm the drop, then hand run() the names to write."""
    clashes = [n for n, f in zip(names, free) if n != f]
    message = f'Copy {counted(names)} ({size(total_size(paths))}) {destination}?\n\n{summarize(names)}'
    if len(clashes) == 1:
        message += f'\n\n{clashes[0]} already exists.'
    elif clashes:
        message += f'\n\n{len(clashes)} names already exist.'

    def answered(answer):
        if answer in ('y', 'o'):
            run(names)
        elif answer == 'r':
            run(free)
        else:
            notify(window, 'Files not copied', summarize(names))

    # Deferred a tick: both callers run inside a kitty callback, and the overlay reenters it.
    def show(timer_id):
        # The ask kitten requires each shortcut letter to occur in its own label.
        if clashes:
            choices = ('r;green:Rename', 'o;red:Overwrite', 'c;yellow:Cancel')
            default = 'r'
        else:
            choices = ('y;green:Copy', 'c;red:Cancel')
            default = 'y'
        get_boss().choose(
            message, answered, *choices,
            window=window, default=default, title='Copy files',
        )

    add_timer(show, 0, False)


def copy_local(window, cwd, paths, names):
    try:
        for p, name in zip(paths, names):
            dest = os.path.join(cwd, name)
            if os.path.isdir(p):
                shutil.copytree(p, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest)
    except OSError as err:
        notify(window, 'Files not copied', str(err))
        return
    notify(window, 'Files copied', summarize(names))


def send_remote(window, paths, names):
    sending[window.id] = summarize(names)
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
        notify(window, 'Files not copied',
               'The remote shell did not answer. The paths were pasted at the prompt.')


def on_set_user_var(boss, window, data):
    if data['key'] != 'kdrop':
        return
    if data['value'] in ('done', 'fail'):
        summary = sending.pop(window.id, '')
        if not summary:
            return
        if data['value'] == 'done':
            notify(window, 'Files copied', summary)
        else:
            notify(window, 'Files not copied', 'The remote could not extract the archive.')
        return
    entry = pending.pop(window.id, None)
    if entry is None:
        return
    ask(window, entry['paths'], entry['names'], data['value'].split('/')[1:],
        'into the remote working directory',
        lambda names: send_remote(window, entry['paths'], names))


def on_drop(self, drop):
    paths = dropped_paths(drop)
    # Alternate screen means a full-screen program owns the terminal, local or remote.
    if not paths or self.screen.is_using_alternate_linebuf():
        return self.original_on_drop(drop)
    names = plan_names(paths)
    if self.child_is_remote:
        pending[self.id] = {'paths': paths, 'names': names, 'drop': drop}
        self.write_to_child(probe(names))
        window_id = self.id
        add_timer(lambda timer_id: expire(window_id), TIMEOUT, False)
    elif self.at_prompt:
        cwd = self.cwd_for_serialization
        ask(self, paths, names, resolve_local(cwd, names), f'into {cwd}',
            lambda chosen: copy_local(self, cwd, paths, chosen))
    else:
        self.original_on_drop(drop)


# A config reload re-runs this file, so keep the first unpatched method.
Window.original_on_drop = getattr(Window, 'original_on_drop', Window.on_drop)
Window.on_drop = on_drop
