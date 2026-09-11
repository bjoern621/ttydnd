"""Carrying a decided plan to its destination.

One transport is one implementation of this domain.
A transport provides three functions:

    rank(destination)                            how well it serves, None when it cannot serve
    resolve(destination, names, ready, decline)  free name per name, in the order they arrive
    send(destination, plan, done, failed)        write the plan

A plan is a (source path, name at the destination) pair per item kept.
Free names follow ttydnd.names, which the transport applies at the far end.
Both calls return a handle taking the destination's answers through reply(value),
or None when the transport finished inside the call.
Which transport runs, and what a new one owes: docs/copy-transports.md.
"""

from . import local, tty

# Every transport a destination is offered to.
TRANSPORTS = [tty, local]

# Terminal id to the handle waiting for that destination's answer.
inflight = {}

# Shown when the destination is one no transport serves.
UNREACHABLE = 'No way to copy files here, so the paths were pasted'


def choose(destination):
    """Highest ranked transport for this destination. None when none serves it."""
    best = rank = None
    for transport in TRANSPORTS:
        score = transport.rank(destination)
        if score is not None and (rank is None or score > rank):
            best, rank = transport, score
    return best


def resolve(destination, names, ready, decline):
    """Ask the destination which of these names are free.

    ready() takes one free name per name, in order.
    decline() takes the reason the destination cannot receive, which a silent remote also reaches.
    """
    transport = choose(destination)
    if transport is None:
        decline(UNREACHABLE)
        return
    _run(destination, lambda done, failed: transport.resolve(destination, names, done, failed),
         ready, decline)


def send(destination, plan, done, failed):
    """Write the plan. failed() takes the reason, ready to show."""
    transport = choose(destination)
    if transport is None:
        failed(UNREACHABLE)
        return
    _run(destination, lambda ok, err: transport.send(destination, plan, ok, err), done, failed)


def _run(destination, call, ok, err):
    terminal_id = destination.terminal.id

    def done(*answer):
        inflight.pop(terminal_id, None)
        ok(*answer)

    def failed(reason):
        inflight.pop(terminal_id, None)
        err(reason)

    handle = call(done, failed)
    if handle is not None:
        inflight[terminal_id] = handle


def reply(terminal_id, value):
    """Route a destination's answer to the handle waiting for it."""
    handle = inflight.get(terminal_id)
    if handle is not None:
        handle.reply(value)


def active(terminal_id):
    """True between the first call and its answer, while the transport owns the terminal."""
    return terminal_id in inflight
