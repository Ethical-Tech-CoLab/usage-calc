"""Derived measures: days, sittings, the person/model split, and groupings.

THE ONE IDEA WORTH CARRYING AWAY from this module is that it publishes two
kinds of number and says which is which.

    MODEL TIME IS MEASURED.  It is a union of intervals the client recorded.
    PERSON TIME IS INFERRED.  It is what is left of a sitting once the model
                              was not generating, and that is not the same
                              thing as a person being there.

The residual errs in BOTH directions. It over-counts when somebody walked
away mid-sitting, and it under-counts the reading done after a sitting's last
request - which, for a research project, is exactly when the reading happens.
It is published anyway, next to the measurement, because a reader can discount
a number they can see the shape of. What they cannot discount is a number that
was quietly folded into "active time" and presented as one thing.

The sensitivity is the argument. Model time does not move when the idle
cut-off changes, because it is measured. Person time moves by a factor of two
or three across the offered range, because it is a residual of an arbitrary
choice. Showing both across a selector lets a reader see which is which
without being asked to take anyone's word for it.
"""

import datetime as dt

from .intervals import (busy_intervals, clip, intersect, merge, sitting_intervals,
                        span)
from .store import StoreError, parse_ts, tok, usd

# The pause lengths offered to a reader. 5 minutes is the default because it is
# long enough to survive reading a paragraph and short enough not to swallow a
# coffee break; the point of offering four is that no single choice is right.
IDLE_CUTOFFS = (120, 300, 600, 1800)
DEFAULT_CUTOFF = 300
CHANNELS = ("input", "cache_read", "cache_write", "output")


def active_time(events):
    """Gap-based active time, per cut-off. Kept for the headline figure.

    This measures gaps BETWEEN requests; `daily` measures sittings that start
    when their first request started. The two differ by a little, and the
    difference is stated on the page rather than hidden.
    """
    ts = sorted(e["ts"] for e in events)
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    out = []
    for cut in IDLE_CUTOFFS:
        out.append({
            "cutoff_s": cut,
            "active_s": round(sum(g for g in gaps if g <= cut), 1),
            "sittings": sum(1 for g in gaps if g > cut) + 1,
        })
    return out


def local_day_bounds(ts):
    """Epoch seconds for local midnight either side of the instant at ts."""
    local = dt.datetime.fromtimestamp(ts).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + dt.timedelta(days=1)).timestamp()


def local_zone():
    """What the platform calls the local zone, and its offset now.

    Reported rather than assumed: a day-by-day table is meaningless until the
    reader knows whose midnight was used to cut it.
    """
    now = dt.datetime.now().astimezone()
    off = now.utcoffset() or dt.timedelta(0)
    mins = int(off.total_seconds() // 60)
    return {
        "name": now.tzname(),
        "utc_offset": "%+03d:%02d" % (mins // 60, abs(mins) % 60),
    }


def daily(events, turns):
    """One row per local calendar day on which a request was issued.

    THE DAY BOUNDARY IS LOCAL, NOT UTC, and this is not a nicety. In the
    project this package came from, 19 per cent of requests land between 00:00
    and 04:00 UTC - the previous evening in New York. Splitting on UTC days
    filed a fifth of the work under the wrong date, and moved one day by a
    factor of five: 404 requests by UTC day against 80 by local day.

    Local days come from the PLATFORM's zone database via astimezone(), not
    from zoneinfo: zoneinfo needs the tzdata package, which a clean Windows
    Python does not have, and a build script that dies on a bare interpreter
    is a landmine. astimezone() also gets daylight saving right, which a fixed
    offset cannot.

    Within a sitting either a model was working or it was not, so

        engaged = model + person

    holds by construction and the person figure can never come out negative.
    That identity is what makes the split defensible. What it does NOT do is
    prove a person was present.
    """
    busy = busy_intervals(events)
    union_total = span(busy)
    sits = {c: sitting_intervals(events, c) for c in IDLE_CUTOFFS}

    buckets = {}
    for e in events:
        key = dt.datetime.fromtimestamp(e["ts"]).astimezone().date().isoformat()
        buckets.setdefault(key, []).append(e)

    starts = {}
    for t in turns:
        d = parse_ts(t["started"]).astimezone().date().isoformat()
        starts[d] = starts.get(d, 0) + 1

    rows = []
    for day in sorted(buckets):
        evs = buckets[day]
        lo, hi = local_day_bounds(evs[0]["ts"])
        times = {}
        for c in IDLE_CUTOFFS:
            eng = clip(sits[c], lo, hi)
            mod = intersect(busy, eng)
            e_s, m_s = span(eng), span(mod)
            times[str(c)] = {
                "engaged_s": round(e_s, 1),
                "model_s": round(m_s, 1),
                "person_s": round(max(0.0, e_s - m_s), 1),
                "sittings": len(eng),
            }
        rows.append({
            "date": day,
            "requests": len(evs),
            "turns_started": starts.get(day, 0),
            "nano_aiu": sum(e["nano"] for e in evs),
            "usd": round(usd(sum(e["nano"] for e in evs)), 2),
            "tokens": sum(tok(e, k) for e in evs for k in CHANNELS),
            "first": dt.datetime.fromtimestamp(evs[0]["ts"]).astimezone().isoformat(),
            "last": dt.datetime.fromtimestamp(evs[-1]["ts"]).astimezone().isoformat(),
            "times": times,
        })

    # Model time cannot depend on how long a pause has to be before it stops
    # counting - it is a union of intervals that were measured. If it moves
    # with the cut-off, the interval arithmetic is double-counting somewhere.
    # This assertion is how the overlapping-sittings bug was found, and it is
    # why the package refuses to write output rather than warn.
    for c in IDLE_CUTOFFS:
        got = sum(r["times"][str(c)]["model_s"] for r in rows)
        if abs(got - union_total) > 1.0:
            raise StoreError(
                "daily model time sums to %.1f s at the %d s cut-off but the "
                "measured union is %.1f s. The interval arithmetic is wrong."
                % (got, c, union_total))
    return rows


def merged_turns(events):
    """One entry per distinct turn, stamped when that turn's first request ran.

    `daily()` counts turns by counting the entries handed to it, so passing one
    entry per REQUEST reports one turn per request - which read as 7,964 turns
    against a true 121 the first time a merge was written. Turn keys must
    already be namespaced by session before they get here, so that distinct
    turns on different machines cannot collide.
    """
    first = {}
    for e in events:
        k = e.get("turn")
        if k is None:
            continue
        if k not in first or e["ts"] < first[k][0]:
            first[k] = (e["ts"], e["at"])
    return [{"started": at} for _, at in sorted(first.values())]


def group(events, key, label="key"):
    """Totals by model, by initiator, by whatever field is asked for."""
    out = {}
    for e in events:
        k = e[key]
        d = out.setdefault(k, {
            label: k, "requests": 0, "nano_aiu": 0, "duration_ms": 0,
            "reasoning": 0, "input": 0, "cache_read": 0, "cache_write": 0,
            "output": 0,
        })
        d["requests"] += 1
        d["nano_aiu"] += e["nano"]
        d["duration_ms"] += e["duration_ms"]
        d["reasoning"] += e["reasoning"]
        for c in CHANNELS:
            d[c] += tok(e, c)
    for d in out.values():
        d["usd"] = round(usd(d["nano_aiu"]), 4)
    return sorted(out.values(), key=lambda d: -d["nano_aiu"])


def counterfactual(events):
    """What the same traffic would have cost with no prompt cache.

    Every cached token is charged at the model's own full input price instead
    of its cache-read or cache-write price. Output is untouched. This is a
    price counterfactual, not a behaviour one: without caching the work would
    likely have been done differently, so read it as an upper bound on what
    caching saved rather than as a bill that was avoided.
    """
    price_in, real, naive = {}, 0, 0
    for e in events:
        c = e["chans"]
        if "input" in c:
            price_in[e["model"]] = c["input"]["price_nano_per_token"]
    missing = sorted({e["model"] for e in events} - set(price_in))
    for e in events:
        real += e["nano"]
        p = price_in.get(e["model"])
        for kind, ch in e["chans"].items():
            if kind == "output" or p is None:
                naive += ch["nano_aiu"]
            else:
                naive += ch["tokens"] * p
    return {
        "actual_usd": round(usd(real), 2),
        "uncached_usd": round(usd(naive), 2),
        "ratio": round(naive / real, 3) if real else None,
        "models_without_an_input_price_sample": missing,
    }
