"""Transport: a filesystem copy into a directory this process can write."""

import os
import shutil

from .. import names


def rank(destination):
    return 1 if destination.kind == 'directory' else None


def free_names(directory, wanted):
    """Free name per wanted name, in order."""
    free = []
    for name in wanted:
        free.append(names.free(name, lambda c: os.path.exists(os.path.join(directory, c)) or c in free))
    return free


def resolve(destination, wanted, ready, decline):
    ready(free_names(destination.path, wanted))


def send(destination, plan, done, failed):
    terminal = destination.terminal
    for path, name in plan:
        dest = os.path.join(destination.path, name)
        try:
            if os.path.isdir(path):
                shutil.copytree(path, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(path, dest)
        except OSError as err:
            terminal.progress(2)
            failed(f'Could not copy {name}. {err.strerror or err}')
            return
    terminal.progress(0)
    done()
