# Writing a copy transport

A transport is one implementation of the copy domain: one way of getting decided files to a
destination.
This page is the code contract for writing one.
Where the domain sits, and what reaches it: [architecture.md](architecture.md).

## What a transport provides

A transport is a module holding three functions.

```python
def rank(destination):
    """How well this transport serves the destination. None when it cannot serve it at all."""

def resolve(destination, names, ready, decline):
    """Answer which of these names are free at the destination.

    ready() takes one free name per name, in the order they arrived.
    decline() takes the reason the destination cannot receive, ready to show.
    """

def send(destination, plan, done, failed):
    """Write the plan: a (source path, name at the destination) pair per item."""
```

Both calls return a handle, or None when the transport finished inside the call.
A handle takes what the destination sends back through `reply(value)`, and the transport calls one
of the two callbacks once the answer settles it.

The copy domain keeps at most one handle per terminal, and routes the destination's answers to it.
A terminal holding a handle is busy: its marker belongs to the transport until the callback fires.

## Choosing one

Every transport in the list is asked to rank the destination, and the highest rank carries the drop.
A rank of None means this transport cannot serve that destination at all.
Ranks separate transports that both serve one destination: an scp transport reaching the session's
host on its own ranks above one that types the files through the session.

A destination no transport serves falls back to the terminal's own handling.

## Free names

Every transport answers `resolve` by the same rule: a taken name counts up from `name-1` until the
destination has nothing at that name.
A transport that runs the rule at the far end, in a shell or on another host, keeps it identical
there, since the confirm domain compares what comes back against what it sent and shows the
difference as a clash.
The suite runs both ends and compares them.

## What a transport may ask of the terminal

`destination.terminal` is the port the backend offers.
A transport uses four of its calls: `write(data)` to the child, `after(seconds, run)` for a timer,
`progress(state)` for the marker, and `alive` to stop work on a window that has closed.
The rest of the port belongs to the confirm domain.

Marker states are 3 while the transport works, 2 when it ends in a failure, and 0 once it lets go.

## Ranking, in practice

`kind` is what a destination is ranked on.
A `directory` destination is a path this process can write.
A `session` destination is a shell the terminal types at, and its working directory is whatever the
shell reports, which nothing on this end can read.
A transport that needs more than the kind, such as a host name for a connection of its own, reads it
from the destination the backend built.
