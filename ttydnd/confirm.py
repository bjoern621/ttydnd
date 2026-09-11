"""What gets written, and under which name.

A drop arrives as paths.
Each item is named, checked against the names already taken at the destination, and put in front of
the user as its own dialog.
What comes out is a plan: a (source path, name at the destination) pair per item kept.

Nothing is written before the last answer, and Esc on any item cancels the drop.
"""

import os

from . import copy, names


def plan_names(paths):
    """Basename per path, suffixed where two dropped items share one."""
    wanted = []
    for path in paths:
        wanted.append(names.free(os.path.basename(path.rstrip('/')), wanted.__contains__))
    return wanted


def total_size(paths):
    total = 0
    for path in paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
        else:
            total += os.path.getsize(path)
    return total


def size(count):
    return f'{count / 1e6:.1f} MB' if count >= 1e6 else f'{max(1, count // 1000)} kB'


def start(destination, paths):
    """Take the drop from paths to a plan the copy domain carries."""
    terminal = destination.terminal
    wanted = plan_names(paths)

    def ready(spare):
        ask(destination, paths, wanted, spare)

    def decline(reason):
        terminal.fallback()
        terminal.tell(reason)

    copy.resolve(destination, wanted, ready, decline)


def ask(destination, paths, wanted, spare):
    """One dialog per item, then the plan goes to the copy domain."""
    terminal = destination.terminal
    where = destination.label
    plan = []
    total = len(paths)

    def decide(i):
        if i == total:
            if plan:
                copy.send(destination, plan, lambda: None, terminal.tell)
            return
        path, name, free = paths[i], wanted[i], spare[i]
        item = f'{name} ({size(total_size([path]))})'
        note = f'Item {i + 1} of {total}. Esc cancels the drop.' if total > 1 else 'Esc cancels the drop.'
        # The destination takes its own line, since a path has no space to wrap at.
        # A dialog requires each shortcut letter to occur in its own label.
        if name == free:
            message = f'Copy {item} into\n{where}?\n\n{note}'
            choices = ('y;green:Copy', 's;yellow:Skip')
            default = 'y'
        else:
            message = f'{item} already exists in\n{where}.\nKeep both writes {free}.\n\n{note}'
            choices = ('k;green:Keep both', 'o;red:Overwrite', 's;yellow:Skip')
            default = 'k'

        def answered(answer):
            if answer in ('y', 'o'):
                plan.append((path, name))
            elif answer == 'k':
                plan.append((path, free))
            elif answer != 's':
                return
            terminal.after(0, lambda: decide(i + 1))

        terminal.ask(message, choices, default, answered)

    # Deferred a tick: the callers and every answer run inside a terminal callback, and a dialog reenters it.
    terminal.after(0, lambda: decide(0))
