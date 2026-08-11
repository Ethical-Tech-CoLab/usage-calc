"""Merging usage from other machines.

THE STORE IS PER MACHINE. There is no API that can ask another machine what it
spent - the store is a local SQLite file. So `usage-calc export` runs over
there and drops a small JSON file into the project, and this module merges it.

ONE RULE GOVERNS EVERYTHING HERE: MONEY IS ADDITIVE AND A PERSON IS NOT.

    requests, tokens, cost   SUM     two machines spending at once spend twice
    model WORK seconds       SUM     two models really were generating
    model WALL time          UNION   only one minute of clock passed
    engaged time             UNION   there is one person, and they cannot be
                                     at two keyboards at once
    person time              RECOMPUTED from the merged clock, never summed

Summing person-hours per machine is the error this module exists to prevent.
It would inflate the weakest column on the page, and it would do it invisibly:
every figure would still reconcile, because both sides of the identity would
have grown together.

AND THE CUT-OFF BELONGS TO THE PERSON, NOT THE KEYBOARD. Cutting each source's
stream into sittings separately and then unioning them applies the idle
cut-off per machine - so a three-minute gap counts as engaged when both
requests land on one machine and as a pause when the second lands on the
other, which is the same person turning to the other screen. That contradicts
the rule above while appearing to implement it. The cut is therefore taken
over the POOLED stream. Measured on a real two-machine project it reads about
nine minutes higher at the five-minute cut-off and twenty-one minutes higher
at thirty, and THE GAP WIDENS WITH THE CUT-OFF - which is the signature of the
mechanism rather than of rounding, because a longer cut-off bridges more gaps
and so bridges more cross-machine ones. Both readings are published so the
choice stays checkable.
"""

import datetime as dt
import json
import os

from .intervals import busy_intervals, intersect, merge, sitting_intervals, span
from .metrics import CHANNELS, IDLE_CUTOFFS, daily, group, merged_turns
from .store import StoreError, tok, usd

FORMAT = "usage-calc-contribution"
# Files written by this package's predecessor inside one project. Accepted so
# that a project cutting over to usage-calc does not have to go back to the
# other machine and re-export everything it already holds.
LEGACY_FORMATS = ("mbd-usage-contribution",)
VERSION = 1


def read_contributions(directory, primary_sid=None, quiet=False):
    """Load every contribution file in `directory`, or return [].

    A contribution is trusted only as far as it checks out. The format and
    version must match; the per-request costs must sum to the stated total;
    the row count must match the stated count. A file failing any of these is
    REFUSED rather than quietly half-counted, because a truncated export
    produces a number that looks entirely reasonable.
    """
    if not directory or not os.path.isdir(directory):
        return []
    accepted = (FORMAT,) + LEGACY_FORMATS
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("format") not in accepted:
            raise StoreError("%s is not a usage contribution file (format %r)"
                             % (name, c.get("format")))
        if c.get("version") != VERSION:
            raise StoreError("%s is contribution version %s; this reader "
                             "handles %d" % (name, c.get("version"), VERSION))
        if primary_sid and c.get("session_id") == primary_sid:
            # The primary session is read live from the store. Counting an
            # export of it as well would double every figure on the page.
            if not quiet:
                print("  skipping %s: it is this session, already counted" % name)
            continue
        out.append({"meta": c, "events": _events_of(c, name), "file": name})
    return out


def _events_of(c, name):
    cols = c["columns"]
    idx = {k: cols.index(k) for k in cols}
    events, tot = [], 0
    for r in c["requests"]:
        g = lambda k: r[idx[k]] if k in idx else 0
        nano = r[idx["nano"]]
        tot += nano
        ts = r[idx["ts"]]
        events.append({
            "ts": ts,
            "at": dt.datetime.fromtimestamp(ts, dt.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "duration_ms": r[idx["duration_ms"]],
            "model": r[idx["model"]],
            # Namespaced by session so that turn 3 on one machine and turn 3 on
            # another cannot collide in a set.
            "turn": "%s:%s" % (c["session_id"][:8], r[idx["turn"]]),
            "agent_id": "sub" if g("sub") else None,
            "initiator": "imported",
            "effort": None,
            "multiplier": None,
            "reasoning": g("reasoning"),
            "nano": nano,
            "chans": {k: {"tokens": g(k)} for k in CHANNELS if g(k)},
        })
    stated = c["totals"]["nano_aiu"]
    if tot != stated:
        raise StoreError(
            "%s: its %d requests sum to %d nano-AIU but the file states %d. "
            "Truncated or edited; refusing to merge it."
            % (name, len(events), tot, stated))
    if len(events) != c["totals"]["requests"]:
        raise StoreError("%s: %d request rows but the file states %d"
                         % (name, len(events), c["totals"]["requests"]))
    events.sort(key=lambda e: e["ts"])
    return events


def _source_channels(source):
    """One source's spend split by billing channel, pooled across its models.

    THE SPLIT COMES FROM WHATEVER THE SOURCE ITSELF STATES, and the two kinds of
    source state it differently. The primary is read live from the store, so
    every request carries its own per-channel cost and the split is summed from
    the rows. A contribution carries per-request TOKENS but not per-request
    cost - the exporter drops the price, because a price table is the one thing
    that can go stale between machines - so its split is taken from the
    file-level aggregate the exporter wrote alongside it.

    Both are exact. The aggregate is checked against the source's own stated
    total here, so a file whose split does not add up to its own bill is
    refused rather than merged into a page that would still look reasonable.
    """
    out = {k: {"tokens": 0, "nano_aiu": 0} for k in CHANNELS}
    stated = sum(e["nano"] for e in source["events"])

    agg = source.get("channel_totals")
    if agg is None:
        for e in source["events"]:
            for kind, ch in e["chans"].items():
                if "nano_aiu" not in ch:
                    raise StoreError(
                        "%s: a request row carries no per-channel cost and the "
                        "source states no channel totals either, so its money "
                        "cannot be split by channel"
                        % source["label"])
                out[kind]["tokens"] += ch["tokens"]
                out[kind]["nano_aiu"] += ch["nano_aiu"]
    else:
        for row in agg:
            k = row["type"]
            if k not in out:
                raise StoreError("%s: unknown billing channel %r"
                                 % (source["label"], k))
            out[k]["tokens"] += row["tokens"]
            out[k]["nano_aiu"] += row["nano_aiu"]

    got = sum(v["nano_aiu"] for v in out.values())
    if got != stated:
        raise StoreError(
            "%s: its channel split comes to %d nano-AIU but its requests sum "
            "to %d. Refusing to merge a split that does not add up to its own "
            "bill." % (source["label"], got, stated))
    return out


def _price_table(events):
    """What each model charges per token on each channel, as observed.

    Only the primary's rows carry prices, so this is learned there and applied
    to everything. It is not a published rate card and must not be treated as
    one: a model that never ran on this machine has no entry, and a model whose
    price changed mid-project would be recorded at whichever rate its rows
    state. `_check_prices` proves the table against every source before any
    figure derived from it is published.
    """
    price = {}
    for e in events:
        for kind, ch in e["chans"].items():
            if "price_nano_per_token" in ch:
                price[(e["model"], kind)] = ch["price_nano_per_token"]
    return price


def _by_model_channel(events):
    """tokens by (model, channel) and total cost by model, from request rows."""
    toks, nano = {}, {}
    for e in events:
        nano[e["model"]] = nano.get(e["model"], 0) + e["nano"]
        for k in CHANNELS:
            n = tok(e, k)
            if n:
                toks[(e["model"], k)] = toks.get((e["model"], k), 0) + n
    return toks, nano


def _fleet_counterfactual(sources, price):
    """What the pooled traffic would have cost with no prompt cache.

    Same price counterfactual as the single-machine one - every cached token
    charged at its model's full input price, output untouched - but it has to
    cross machines, and a contribution states no prices. So the primary's
    observed table is applied to the other machines' token counts, and the
    application is PROVED before it is used: for every model whose channels are
    all priced, the table must reproduce that model's stated cost on that
    source exactly. A model the table cannot price is not guessed at and not
    dropped either; it is charged at what it actually cost, which makes the
    result a LOWER bound for that sliver, and it is named in the payload.
    """
    real = naive = 0
    unpriced = set()
    for s in sources:
        toks, nano = _by_model_channel(s["events"])
        for model, actual in nano.items():
            used = [k for k in CHANNELS if toks.get((model, k))]
            if any((model, k) not in price for k in used):
                unpriced.add(model)
                real += actual
                naive += actual          # no uplift: a floor, never a guess
                continue
            recon = sum(toks[(model, k)] * price[(model, k)] for k in used)
            if recon != actual:
                raise StoreError(
                    "%s: repricing %s from the primary's observed rates gives "
                    "%d nano-AIU against a stated %d. The price table does not "
                    "describe this source, so no counterfactual is published "
                    "for it." % (s["label"], model, recon, actual))
            real += actual
            p_in = price[(model, "input")]
            for k in used:
                naive += (toks[(model, k)] * price[(model, k)] if k == "output"
                          else toks[(model, k)] * p_in)
    return {
        "actual_usd": round(usd(real), 2),
        "uncached_usd": round(usd(naive), 2),
        "ratio": round(naive / real, 3) if real else None,
        "models_without_an_input_price_sample": sorted(unpriced),
    }


def fleet(primary_label, primary_meta, primary_events, contribs):
    """Everything spent on a project, across every machine that worked on it.

    Returns None when there are no contributions, so a caller can fall back to
    naming what it cannot see rather than presenting a single machine's total
    as the project's.
    """
    if not contribs:
        return None

    sources = [{"label": primary_label, "meta": primary_meta,
                "events": primary_events, "primary": True,
                "channel_totals": None}]
    for c in contribs:
        sources.append({
            "label": c["meta"].get("project") or c["file"],
            "meta": {"machine": c["meta"].get("machine"),
                     "repository": c["meta"].get("repository"),
                     "session": c["meta"].get("session_id"),
                     "file": c["file"]},
            "events": c["events"],
            "primary": False,
            # Per-request rows in a contribution carry tokens but no cost, so
            # the channel split has to come from the aggregate the exporter
            # wrote. A file predating that field states None and is refused by
            # _source_channels rather than merged with a channel split missing.
            "channel_totals": c["meta"].get("channels"),
        })

    rows, all_events, all_busy = [], [], []
    price = _price_table(primary_events)
    for s in sources:
        evs = s["events"]
        busy = busy_intervals(evs)
        nano = sum(e["nano"] for e in evs)
        rows.append({
            "project": s["label"],
            "machine": s["meta"].get("machine") or "this machine",
            "primary": s["primary"],
            "requests": len(evs),
            "turns": len({e["turn"] for e in evs if e["turn"] is not None}),
            "subagent_requests": sum(1 for e in evs if e.get("agent_id")),
            "nano_aiu": nano,
            "usd": round(usd(nano), 2),
            "tokens": sum(tok(e, k) for e in evs for k in CHANNELS),
            "model_s": round(span(busy), 1),
            "first": dt.datetime.fromtimestamp(evs[0]["ts"]).astimezone().isoformat(),
            "last": dt.datetime.fromtimestamp(evs[-1]["ts"]).astimezone().isoformat(),
            "days": len({dt.datetime.fromtimestamp(e["ts"]).astimezone().date()
                         for e in evs}),
            "models": sorted({e["model"] for e in evs}),
            # This source's own money split, by model and by billing channel,
            # so a reader who picks one repository gets that repository's split
            # rather than the pooled one under its name.
            "by_model": group(evs, "model", "model"),
            "by_channel": _source_channels(s),
            "counterfactual": _fleet_counterfactual([s], price),
            # Day rows for THIS repository alone, so the page can offer a
            # per-repository scope instead of only all-or-primary.
            #
            # These do not add up to the merged rows and must never be
            # presented as if they did. Cost and requests would sum correctly,
            # but engaged time is cut into sittings over this repository's
            # stream ALONE, so a pause spent in a sibling repository reads as
            # idle here and as engaged there. The merged view cuts the pooled
            # stream, which is the only reading that matches one person. The
            # difference is published on the page as the bridged figure.
            "day_rows": daily(evs, merged_turns(evs)),
        })
        all_events.extend(evs)
        all_busy.extend(busy)

    all_events.sort(key=lambda e: e["ts"])
    union_busy = merge(all_busy)
    model_wall = span(union_busy)
    model_work = sum(r["model_s"] for r in rows)

    times = {}
    for c in IDLE_CUTOFFS:
        # Pooled, per the module docstring. The per-machine reading is kept
        # beside it so the difference is visible rather than asserted.
        sits = sitting_intervals(all_events, c)
        eng = span(sits)
        mod = span(intersect(union_busy, sits))
        times[str(c)] = {
            "engaged_s": round(eng, 1),
            "model_s": round(mod, 1),
            "person_s": round(max(0.0, eng - mod), 1),
            "sittings": len(sits),
            "engaged_per_machine_s": round(
                span(merge([iv for s in sources
                            for iv in sitting_intervals(s["events"], c)])), 1),
        }

    total_nano = sum(r["nano_aiu"] for r in rows)

    # The money split pooled across every machine. Channels are additive in the
    # way the module docstring means - a token bought on one machine and a
    # token bought on another are two tokens - so unlike time these simply sum.
    # The sum is checked against the fleet total, which is what proves no
    # source's split was dropped or double-counted.
    chan = {k: {"type": k, "tokens": 0, "nano_aiu": 0} for k in CHANNELS}
    for r in rows:
        for k, v in r["by_channel"].items():
            chan[k]["tokens"] += v["tokens"]
            chan[k]["nano_aiu"] += v["nano_aiu"]
    channels = [c for c in chan.values() if c["tokens"] or c["nano_aiu"]]
    for c in channels:
        c["usd"] = round(usd(c["nano_aiu"]), 4)
    got = sum(c["nano_aiu"] for c in channels)
    if got != total_nano:
        raise StoreError(
            "the merged channel split comes to %d nano-AIU against a fleet "
            "total of %d" % (got, total_nano))
    channels.sort(key=lambda c: -c["nano_aiu"])

    models = group(all_events, "model", "model")
    got = sum(m["nano_aiu"] for m in models)
    if got != total_nano:
        raise StoreError(
            "the merged model split comes to %d nano-AIU against a fleet "
            "total of %d" % (got, total_nano))

    return {
        "sources": sorted(rows, key=lambda r: -r["nano_aiu"]),
        "totals": {
            "projects": len(rows),
            "machines": len({r["machine"] for r in rows}),
            "requests": sum(r["requests"] for r in rows),
            "turns": sum(r["turns"] for r in rows),
            "nano_aiu": total_nano,
            "usd": round(usd(total_nano), 2),
            "tokens": sum(r["tokens"] for r in rows),
            "days": len({dt.datetime.fromtimestamp(e["ts"]).astimezone().date()
                         for e in all_events}),
            "subagent_requests": sum(r["subagent_requests"] for r in rows),
            "models": len(models),
        },
        "models": models,
        "channels": channels,
        "counterfactual": _fleet_counterfactual(sources, price),
        "time": {
            "model_work_s": round(model_work, 1),
            "model_wall_s": round(model_wall, 1),
            "concurrent_s": round(model_work - model_wall, 1),
            "span_s": round(all_events[-1]["ts"] - all_events[0]["ts"], 1),
            "cutoffs": list(IDLE_CUTOFFS),
            "default_cutoff_s": 300,
            "times": times,
        },
        "days": daily(all_events, merged_turns(all_events)),
        "note": (
            "Money is additive and a person is not. Requests, tokens and cost "
            "are summed across machines. Wall-clock time is not: model time is "
            "the union of intervals across every machine, and engaged time is "
            "the union of sittings, because one person cannot be at two "
            "keyboards at once. Summing person-hours per machine would inflate "
            "the weakest column on this page and do it invisibly."
        ),
    }
