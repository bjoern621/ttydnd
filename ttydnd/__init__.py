"""Dropped files reaching a shell's working directory.

Three domains, each replaceable on its own:

    drop     a terminal backend turns a drop into paths and a Destination,
             and offers the terminal port the other two call back on
    confirm  names the items, asks about each one, decides the plan
    copy     carries a plan to its destination through a transport

A backend calls the three functions below and reaches nothing else in here.
Contracts: docs/architecture.md.
"""

from . import confirm, copy


class Destination:
    """Where a drop lands, as the backend sees it.

    kind: "directory" for a path this process can write, "session" for a shell the terminal types at.
    path: the directory, for kind "directory".
    terminal: the port a transport writes to and waits on.
    """

    def __init__(self, terminal, kind, path=None):
        self.terminal = terminal
        self.kind = kind
        self.path = path

    @property
    def label(self):
        """How a dialog names the place."""
        return self.path if self.path else 'the remote working directory'


def dropped(destination, paths):
    """Take a drop through confirmation and copying."""
    confirm.start(destination, paths)


def answered(terminal_id, value):
    """Hand a destination's reply to whichever transport is waiting on it."""
    copy.reply(terminal_id, value)


def busy(terminal_id):
    """True while a transport holds this terminal, so its marker is not the backend's to clear."""
    return copy.active(terminal_id)
