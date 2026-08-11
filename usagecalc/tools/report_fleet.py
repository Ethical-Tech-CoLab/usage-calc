#!/usr/bin/env python3
"""Report merged usage across contribution files, without building a dashboard.

This is a READ-ONLY reporter, and it exists for one specific situation that is
easy to get wrong.

`usage-calc build` produces a page whose PRIMARY session is read from the local
store. On a machine that holds contributions but no session for the project
being reported on, running the full generator would publish a dashboard
headlined by whichever session that machine does hold - a real project, a
plausible-looking page, and the wrong subject. So this reads the contribution
files only, validates them with the package's own checker, and prints.

It applies the package's time semantics:

    money is additive and a person is not.

Requests, tokens and cost are summed. Model WORK seconds are summed, because two
sessions generating at once really are two models working. Model WALL time is a
union, because only one clock ran. Engaged time is a union of sittings over the
POOLED stream, because there is one person and the idle cut-off describes them
rather than a keyboard.

Usage:  python report_fleet.py [contrib-directory]
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usagecalc.contrib import read_contributions  # noqa: E402
from usagecalc.intervals import (busy_intervals, intersect, merge,  # noqa: E402
                                 sitting_intervals, span)
from usagecalc.metrics import IDLE_CUTOFFS  # noqa: E402
from usagecalc.store import tok, usd  # noqa: E402

NANO = 1_000_000_000
CHANNELS = ("input", "cache_read", "cache_write", "output")


def human(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %02dm" % (h, m)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s


def main():
    where = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "usage", "contrib")
    # A session id that cannot match anything, so nothing is skipped as "the live session".
    contribs = read_contributions(where, primary_sid="__report_only__")
    if not contribs:
        print("no contribution files in %s" % where)
        return 1

    rows, all_busy, all_events = [], [], []
    for c in contribs:
        evs = c["events"]
        meta = c["meta"]
        busy = busy_intervals(evs)
        nano = sum(e["nano"] for e in evs)
        rows.append({
            "project": meta.get("project") or c["file"],
            "machine": meta.get("machine"),
            "session": meta.get("session_id", "")[:8],
            "requests": len(evs),
            "turns": meta["totals"].get("turns", 0),
            "sub": sum(1 for e in evs if e.get("agent_id")),
            "nano": nano,
            "tokens": sum(tok(e, k) for e in evs for k in CHANNELS),
            "model_s": span(busy),
            "days": len({dt.datetime.fromtimestamp(e["ts"]).astimezone().date() for e in evs}),
            "first": min(e["ts"] for e in evs),
            "last": max(e["ts"] for e in evs),
            "models": sorted({e["model"] for e in evs}),
        })
        all_busy.extend(busy)
        all_events.extend(evs)

    rows.sort(key=lambda r: -r["nano"])
    all_events.sort(key=lambda e: e["ts"])
    union_busy = merge(all_busy)
    model_wall = span(union_busy)
    model_work = sum(r["model_s"] for r in rows)

    tot_nano = sum(r["nano"] for r in rows)
    tot_req = sum(r["requests"] for r in rows)
    tot_tok = sum(r["tokens"] for r in rows)

    print("=" * 78)
    print("MERGED USAGE ACROSS CONTRIBUTING REPOSITORIES")
    print("=" * 78)
    print()
    # SCOPE, stated before any number. This reads the contributions directory
    # ONLY. On a machine that holds nothing else that is the whole story; on
    # the machine holding the project's own session it omits that session, so
    # these totals are LOWER than the dashboard's and legitimately so.
    print("Scope: %d contribution file(s) in %s." % (len(contribs), where))
    print("This reporter does NOT read any local session store, so if a session for")
    print("the project lives on the machine you are running this on, it is NOT")
    print("counted below. The published dashboard merges both; this is the")
    print("contribution side of that merge.")
    print()
    print("%-24s %8s %7s %10s %9s %10s" % ("repository", "requests", "turns", "AIU", "USD", "model time"))
    print("-" * 78)
    for r in rows:
        print("%-24s %8d %7d %10.1f %9.2f %10s"
              % (r["project"], r["requests"], r["turns"], r["nano"] / NANO,
                 usd(r["nano"]), human(r["model_s"])))
    print("-" * 78)
    print("%-24s %8d %7d %10.1f %9.2f %10s"
          % ("TOTAL", tot_req, sum(r["turns"] for r in rows), tot_nano / NANO,
             usd(tot_nano), human(model_work)))
    print()

    print("TOKENS")
    chan = {}
    for e in all_events:
        for k in CHANNELS:
            chan[k] = chan.get(k, 0) + tok(e, k)
    for k in CHANNELS:
        share = chan[k] / tot_tok * 100 if tot_tok else 0
        print("  %-12s %15s  %5.1f%%" % (k, "{:,}".format(chan[k]), share))
    print("  %-12s %15s" % ("total", "{:,}".format(tot_tok)))
    reasoning = sum(e.get("reasoning", 0) for e in all_events)
    print("  %-12s %15s  (billed inside output)" % ("reasoning", "{:,}".format(reasoning)))
    print()

    print("MODELS")
    by_model = {}
    for e in all_events:
        d = by_model.setdefault(e["model"], {"requests": 0, "nano": 0})
        d["requests"] += 1
        d["nano"] += e["nano"]
    for name, d in sorted(by_model.items(), key=lambda kv: -kv[1]["nano"]):
        print("  %-28s %6d req  %9.1f AIU  $%8.2f  %5.1f%%"
              % (name, d["requests"], d["nano"] / NANO, usd(d["nano"]),
                 d["nano"] / tot_nano * 100))
    print()

    print("TIME  (money is additive, a person is not)")
    print("  model work    %10s   summed: concurrent sessions really are two models working"
          % human(model_work))
    print("  model wall    %10s   union: only one clock ran" % human(model_wall))
    overlap = model_work - model_wall
    print("  concurrent    %10s   %.1f%% of work time overlapped another session"
          % (human(overlap), (overlap / model_work * 100) if model_work else 0))
    print()
    for cutoff in IDLE_CUTOFFS:
        # Cut the POOLED stream, not each contribution separately. The idle
        # cut-off describes a person, so a gap that counts as engaged within
        # one session must not become a pause merely because the next request
        # landed in another. See the README, "the cut-off belongs to the
        # person, not to the keyboard".
        sits = sitting_intervals(all_events, cutoff)
        eng = span(sits)
        mod = span(intersect(union_busy, sits))
        print("  idle cutoff %4ds  engaged %9s   model %9s   person %9s   %d sittings"
              % (cutoff, human(eng), human(mod), human(max(0.0, eng - mod)), len(sits)))
    print()

    span_first = min(r["first"] for r in rows)
    span_last = max(r["last"] for r in rows)
    days = len({dt.datetime.fromtimestamp(e["ts"]).astimezone().date() for e in all_events})
    print("SPAN")
    print("  first request  %s" % dt.datetime.fromtimestamp(span_first).astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    print("  last request   %s" % dt.datetime.fromtimestamp(span_last).astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    print("  active days    %d" % days)
    print("  cost per day   $%.2f" % (usd(tot_nano) / days if days else 0))
    print()
    print("Contributions validated by usagecalc.contrib.read_contributions():")
    print("  per-request costs reconcile to the stated total, row counts match.")
    print("  No prompt text: every file carries contains_prompt_text=false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
