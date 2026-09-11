"""The free-name rule, applied at both ends of a drop.

A taken name counts up from name-1 until nothing answers to it.
The confirm domain applies the rule to the dropped names, and every transport applies it at its
destination, so the suite runs both and compares.
"""


def split(name):
    # Mirrors the remote shell's ?*.* case, so a leading-dot name keeps all of it as the stem.
    stem, dot, ext = name.rpartition('.')
    return (stem, '.' + ext) if dot and stem else (name, '')


def free(name, taken):
    """First name taken() refuses, counting up from name-1."""
    stem, ext = split(name)
    candidate, i = name, 1
    while taken(candidate):
        candidate = f'{stem}-{i}{ext}'
        i += 1
    return candidate
