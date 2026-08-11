#!/usr/bin/env python3
"""Ask the local store what every session on THIS machine cost.

The dashboard answers one question well - what did *this* project cost - and it
needs contribution files to see past one machine. This script answers the
other question, and needs nothing: what has this machine been doing lately,
across all projects? Nothing has to be collected in advance. The Copilot CLI
writes `assistant_usage_events` as it goes, one row per request, and
`sessions.repository` / `sessions.cwd` say which project each row belongs to.

    python query_sessions.py                 # last 7 days, by session
    python query_sessions.py --days 30       # a longer window
    python query_sessions.py --all           # everything retained
    python query_sessions.py --by day        # calendar days, local zone
    python query_sessions.py --by model
    python query_sessions.py --by repo       # sessions folded by project
    python query_sessions.py --json          # for piping somewhere

WHY THIS IS NOT ONE LINE OF SQL. Two of the obvious aggregations are wrong:

  SUM(duration_ms) OVERSTATES MODEL TIME. Sub-agent requests run BESIDE the
  main agent, not after it, so their durations overlap. On the session this
  was written against the naive sum reads 16.79 h against a true union of
  14.99 h - 12 per cent high. This script unions the intervals.

  SUM(total_nano_aiu) IS THE RIGHT COST but the token columns disagree with
  `token_details_json` on compaction rows. Costs here come from the same
  column the dashboard trusts, and the token split is read from the details.

Days are cut on LOCAL midnight, not UTC. On one real project 19 per cent of
requests landed between 00:00 and 04:00 UTC - the previous evening locally -
and a UTC cut misattributed one day by a factor of five.

Read-only. Copies the store to a temp file first, so it is safe to run while
the CLI is using it.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NANO_PER_AIU = 1_000_000_000
CENTS_PER_AIU = 1  # GitHub documents 1 AI credit = $0.01.


def store_path():
    env = os.environ.get("COPILOT_SESSION_STORE")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".copilot", "session-store.db")


def snapshot(src):
    """Copy the store (and any WAL sidecars) so a live CLI cannot be disturbed."""
    if not os.path.exists(src):
        sys.exit("no session store at %s\n"
                 "Set COPILOT_SESSION_STORE if it lives elsewhere." % src)
    tmp = os.path.join(tempfile.mkdtemp(prefix="usage-q-"), "session-store.db")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(src + ext):
            shutil.copy2(src + ext, tmp + ext)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row
    return con


def parse_ts(s):
    """The store writes ISO-8601 with a trailing Z; make it tz-aware UTC."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def union_hours(events):
    """Wall-clock hours during which AT LEAST ONE request was in flight.

    Not the sum of durations. A sub-agent request occupies the same minutes as
    the main-agent request that spawned it, so summing counts those minutes
    twice - 12 per cent high on a session that leans on sub-agents.
    """
    iv = sorted((e["start"], e["end"]) for e in events if e["end"] > e["start"])
    total, cur_a, cur_b = 0.0, None, None
    for a, b in iv:
        if cur_a is None:
            cur_a, cur_b = a, b
        elif a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            total += cur_b - cur_a
            cur_a, cur_b = a, b
    if cur_a is not None:
        total += cur_b - cur_a
    return total / 3600.0


def tokens_of(row):
    """Prefer token_details_json; fall back to the columns when it is absent.

    The details are a LIST of per-channel entries - each with a tokenType,
    a tokenCount and the rate it was billed at - not an object keyed by
    channel. The two sources disagree on compaction rows, and the details are
    what reconcile to the recorded cost, so they win where both exist.
    """
    out = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    raw = row["token_details_json"]
    if raw:
        try:
            for d in json.loads(raw) or []:
                k = d.get("tokenType")
                if k in out:
                    out[k] += int(d.get("tokenCount") or 0)
            if any(out.values()):
                out["reasoning"] = row["reasoning_tokens"] or 0
                return out
        except (ValueError, TypeError, AttributeError):
            pass
    out["input"] = row["input_tokens"] or 0
    out["output"] = row["output_tokens"] or 0
    out["cache_read"] = row["cache_read_tokens"] or 0
    out["cache_write"] = row["cache_write_tokens"] or 0
    out["reasoning"] = row["reasoning_tokens"] or 0
    return out


def load(con, since):
    rows = con.execute("""
        SELECT e.*, s.repository, s.cwd
        FROM assistant_usage_events e
        LEFT JOIN sessions s ON s.id = e.session_id
        ORDER BY e.created_at
    """).fetchall()
    out = []
    for r in rows:
        ts = parse_ts(r["created_at"])
        if ts is None or (since and ts < since):
            continue
        end = ts.timestamp()
        who = r["repository"] or os.path.basename((r["cwd"] or "").rstrip("\\/")) or "?"
        out.append({
            "session": r["session_id"],
            "project": who,
            "turn": r["turn_index"],
            "model": r["model"] or "?",
            "sub": bool(r["parent_tool_call_id"] or r["agent_id"]),
            "nano": r["total_nano_aiu"] or 0,
            "start": end - (r["duration_ms"] or 0) / 1000.0,
            "end": end,
            "local": ts.astimezone(),
            "tokens": tokens_of(r),
        })
    return out


def summarise(events, key):
    groups = {}
    for e in events:
        groups.setdefault(key(e), []).append(e)
    rows = []
    for k, evs in groups.items():
        tk = {}
        for e in evs:
            for c, v in e["tokens"].items():
                tk[c] = tk.get(c, 0) + v
        billed = tk.get("input", 0) + tk.get("cache_read", 0) + \
            tk.get("cache_write", 0) + tk.get("output", 0)
        rows.append({
            "key": k,
            "requests": len(evs),
            "sub_requests": sum(1 for e in evs if e["sub"]),
            "turns": len({(e["session"], e["turn"]) for e in evs}),
            "sessions": len({e["session"] for e in evs}),
            "models": len({e["model"] for e in evs}),
            "usd": round(evs and sum(e["nano"] for e in evs) / NANO_PER_AIU
                         * CENTS_PER_AIU / 100.0 or 0.0, 2),
            "model_h": round(union_hours(evs), 2),
            "model_h_summed": round(sum(e["end"] - e["start"] for e in evs) / 3600.0, 2),
            "tokens": billed,
            "first": min(e["local"] for e in evs).strftime("%Y-%m-%d %H:%M"),
            "last": max(e["local"] for e in evs).strftime("%Y-%m-%d %H:%M"),
        })
    return sorted(rows, key=lambda r: -r["usd"])


def fmt(rows, label, events, window, zone):
    if not rows:
        print("No usage recorded in that window.")
        return
    wide = max([len(str(r["key"])) for r in rows] + [len(label)])
    wide = min(max(wide, 12), 44)
    head = ("%-*s %7s %6s %9s %8s %13s  %s"
            % (wide, label, "req", "turns", "USD", "model h", "tokens", "last"))
    print(head)
    print("-" * len(head))
    for r in rows:
        print("%-*s %7s %6d %9.2f %8.2f %13s  %s"
              % (wide, str(r["key"])[:wide], "{:,}".format(r["requests"]), r["turns"],
                 r["usd"], r["model_h"], "{:,}".format(r["tokens"]), r["last"]))
    print("-" * len(head))

    total_usd = sum(r["usd"] for r in rows)
    union = union_hours(events)
    summed = sum(e["end"] - e["start"] for e in events) / 3600.0
    print("%-*s %7s %6d %9.2f %8.2f %13s"
          % (wide, "TOTAL", "{:,}".format(len(events)),
             len({(e["session"], e["turn"]) for e in events}), total_usd, union,
             "{:,}".format(sum(sum(e["tokens"][c] for c in
                                   ("input", "cache_read", "cache_write", "output"))
                               for e in events))))
    print()
    print("Window: %s. Days and times in %s." % (window, zone))
    # The per-row model hours are unions within each row, so they do not add to
    # the total union across rows either. Say so rather than let it look wrong.
    if summed - union > 0.01:
        print("Model hours are a UNION of in-flight intervals, not a sum of "
              "durations:\n  summed %.2f h would overstate by %.1f%% because "
              "%d sub-agent requests\n  ran beside a main-agent request rather "
              "than after it."
              % (summed, (summed / union - 1) * 100 if union else 0,
                 sum(1 for e in events if e["sub"])))
    print("Cost is a list-price equivalent at 1 AI credit = $0.01, not a bill.")


def main():
    ap = argparse.ArgumentParser(
        description="Telemetry by session/day/model/repo from the local store.")
    ap.add_argument("--days", type=float, default=7,
                    help="window in days, counting back from now (default 7)")
    ap.add_argument("--all", action="store_true", help="every row retained")
    ap.add_argument("--by", choices=("session", "day", "model", "repo"),
                    default="session", help="how to group (default session)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--store", help="path to a session-store.db")
    args = ap.parse_args()

    con = snapshot(args.store or store_path())
    since = None if args.all else (dt.datetime.now(dt.timezone.utc)
                                   - dt.timedelta(days=args.days))
    events = load(con, since)
    if not events:
        print("No usage rows in that window. The store keeps one row per "
              "request; if this is unexpected, try --all to see what is "
              "retained at all.")
        return 0

    keyf = {
        "session": lambda e: e["project"],
        "repo": lambda e: e["project"],
        "day": lambda e: e["local"].date().isoformat(),
        "model": lambda e: e["model"],
    }[args.by]
    if args.by == "session":
        keyf = lambda e: "%s  [%s]" % (e["project"], e["session"][:8])  # noqa: E731

    rows = summarise(events, keyf)
    if args.by == "day":
        rows.sort(key=lambda r: r["key"])

    zone = dt.datetime.now().astimezone().tzname() or "local time"
    window = ("everything retained (from %s)" % min(e["local"] for e in events)
              .strftime("%Y-%m-%d") if args.all else "last %g days" % args.days)

    if args.json:
        print(json.dumps({
            "generated_at": dt.datetime.now(dt.timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window": window, "zone": zone, "group_by": args.by,
            "model_h_is": "union of in-flight intervals, not a sum of durations",
            "usd_is": "list-price equivalent at 1 AI credit = $0.01, not a bill",
            "rows": rows,
        }, indent=1))
        return 0

    label = {"session": "project  [session]", "repo": "repository",
             "day": "day", "model": "model"}[args.by]
    fmt(rows, label, events, window, zone)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
