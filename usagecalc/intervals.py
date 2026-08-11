"""Interval arithmetic. Every time figure this package publishes comes from here.

The whole point of this module is that WALL-CLOCK TIME IS NOT ADDITIVE. Two
requests that were in flight together occupied one minute of the world, not
two, and the client's store makes it very easy to get this wrong: it records a
`duration_ms` per request, and summing that column is the obvious thing to do.

It overstates. Sub-agent requests run BESIDE the main agent, not after it, so
their durations overlap. On the project this package was extracted from, the
naive sum read 16.79 h against a true union of 14.99 h - 12 per cent high, and
15.2 per cent over a week of heavy sub-agent use. Nothing announces the error;
the number simply comes out too big.

So: model time is a UNION, engaged time is a UNION, and the only quantities
that are ever summed are the ones that really are additive - requests, tokens
and money.
"""


def busy_intervals(events):
    """Merged wall-clock intervals during which at least one request was in flight."""
    iv = sorted((e["ts"] - e["duration_ms"] / 1000.0, e["ts"]) for e in events)
    merged = []
    for a, b in iv:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def busy_union(events):
    """(seconds, block count) during which at least one request was in flight."""
    iv = busy_intervals(events)
    return sum(b - a for a, b in iv), len(iv)


def sitting_intervals(events, cutoff):
    """Clusters of requests, split wherever the pause between two exceeds cutoff.

    A SITTING STARTS WHEN ITS FIRST REQUEST STARTED, not when that request
    returned. If it started at the return instead, the first inference of every
    sitting would fall outside the engaged window and the person/model residual
    could come out negative - which is not a rounding problem but a sign the
    decomposition has been defined incorrectly.
    """
    ev = sorted(events, key=lambda e: e["ts"])
    if not ev:
        return []
    groups, cur = [], [ev[0]]
    for prev, nxt in zip(ev, ev[1:]):
        if nxt["ts"] - prev["ts"] > cutoff:
            groups.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    groups.append(cur)
    return merge([
        (min(e["ts"] - e["duration_ms"] / 1000.0 for e in g), g[-1]["ts"])
        for g in groups
    ])


def merge(intervals):
    """Union of a set of intervals.

    LOAD-BEARING, NOT TIDINESS. Two sittings can overlap: a sub-agent request
    that runs for minutes completes after the pause that ended the previous
    sitting, so the next sitting's start - which is when its first request
    STARTED - can precede the previous sitting's end. Summing such a list
    without merging counts the overlap twice, and the error is small enough to
    hide. It showed up in the original as model hours drifting 12 seconds
    across idle cut-offs they cannot logically depend on at all.

    Note what could NOT find that bug: the identity engaged = model + person.
    Both sides inflated equally, so the identity held throughout. Only a
    paragraph that silently refused to render gave it away.
    """
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def clip(intervals, lo, hi):
    """The parts of intervals that fall inside [lo, hi), merged."""
    out = []
    for a, b in intervals:
        s, e = max(a, lo), min(b, hi)
        if e > s:
            out.append((s, e))
    return merge(out)


def intersect(xs, ys):
    """Overlap of two interval lists, merged. Both are small; O(n*m) is fine."""
    out = []
    for a0, a1 in xs:
        for b0, b1 in ys:
            s, e = max(a0, b0), min(a1, b1)
            if e > s:
                out.append((s, e))
    return merge(out)


def span(intervals):
    """Total seconds covered. Only meaningful on a MERGED list."""
    return sum(b - a for a, b in intervals)
