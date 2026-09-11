# ttydnd kitty backend.
# Turns a drop on a window into paths and a destination, and offers the rest of ttydnd the port it
# writes, asks and reports through.
# Naming, confirmation and copying are terminal agnostic and live in the ttydnd package.
# Domains and their contracts: docs/architecture.md.
# Dragging a name back out is kitty config alone, no code here.

import os
import sys

from kitty.boss import get_boss
from kitty.fast_data_types import add_timer
from kitty.utils import parse_uri_list
from kitty.window import Window

# kitty runs a watcher through runpy, so the package beside it reaches sys.path here or nowhere.
# A rebuild moves the watcher and kitty runs both copies, so what a former copy imported goes first:
# without that, the newer file would run on the older package.
ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE = ROOT if os.path.isdir(os.path.join(ROOT, 'ttydnd')) else os.path.dirname(ROOT)
for stale in [name for name in sys.modules if name == 'ttydnd' or name.startswith('ttydnd.')]:
    del sys.modules[stale]
sys.path.insert(0, PACKAGE)
try:
    import ttydnd
finally:
    sys.path.remove(PACKAGE)

# Heading every dialog and overlay this watcher opens.
TITLE = 'Copy files'

# Window id to the failure overlay showing on it.
reports = {}

# Overlay id to the window beneath, for every dialog opened here.
dialogs = {}


class Terminal:
    """What a kitty window can be asked to do, for the rest of ttydnd.

    One per drop, so fallback() still holds what kitty handed over.
    A window that has closed answers to nothing, and every call goes quiet.
    """

    def __init__(self, window, drop=None):
        self.id = window.id
        self.drop = drop

    @property
    def window(self):
        return get_boss().window_id_map.get(self.id)

    @property
    def alive(self):
        return self.window is not None

    def write(self, data):
        window = self.window
        if window is not None:
            window.write_to_child(data)

    def after(self, seconds, run):
        add_timer(lambda timer_id: run(), seconds, False)

    def progress(self, state):
        """Drive the tab bar marker and the progress bar the way an OSC 9;4 report does.

        3 spins, 2 marks an error, 0 clears.
        """
        window = self.window
        if window is None:
            return
        window.progress.update(state)
        window.screen.set_progress(window.progress.state.value, window.progress.percent)
        tab = window.tabref()
        if tab is not None:
            tab.update_progress()

    def fallback(self):
        """Hand the drop back to kitty, which pastes the paths."""
        window = self.window
        if window is not None and self.drop is not None:
            window.original_on_drop(self.drop)

    def ask(self, message, choices, default, answered):
        window = self.window
        if window is None:
            return
        slot = {}

        def closed(answer):
            dialogs.pop(slot.get('overlay'), None)
            answered(answer)

        overlay = get_boss().choose(message, closed, *choices, window=window, default=default, title=TITLE)
        if overlay is not None:
            slot['overlay'] = overlay.id
            dialogs[overlay.id] = self.id

    def tell(self, text):
        """Show a failure in an overlay on the window until Enter, Esc or a click closes it."""
        report(self.id, text)


def report(window_id, text):
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
        window = get_boss().window_id_map.get(window_id)
        if window is None:
            del reports[window_id]
            return
        overlay = get_boss().choose(text, closed, 'o:OK', window=window, default='o', title=TITLE)
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
    # A transport holding the window keeps its marker.
    if window is not None and not ttydnd.busy(window_id):
        Terminal(window).progress(0)


def dropped_paths(drop):
    uri_list = drop.get('text/uri-list', b'').decode('utf-8', 'replace')
    return [p for p in parse_uri_list(uri_list) if os.path.exists(p)]


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
    terminal = Terminal(self, drop)
    if self.child_is_remote:
        ttydnd.dropped(ttydnd.Destination(terminal, 'session'), paths)
    elif self.at_prompt:
        ttydnd.dropped(ttydnd.Destination(terminal, 'directory', self.cwd_for_serialization), paths)
    else:
        return self.original_on_drop(drop)


def on_set_user_var(boss, window, data):
    # A window keeps the watcher it was created with, while on_drop is the newest copy of this file.
    # An older copy hands the reply on, so a drop and its answer meet in one copy's state.
    current = getattr(Window, 'ttydnd_user_var', None)
    if current is not None and current is not on_set_user_var:
        return current(boss, window, data)
    if data['key'] == 'kdrop':
        ttydnd.answered(window.id, data['value'])


# A config reload re-runs this file, so keep the first unpatched method.
Window.original_on_drop = getattr(Window, 'original_on_drop', Window.on_drop)
Window.on_drop = on_drop
Window.ttydnd_user_var = on_set_user_var
