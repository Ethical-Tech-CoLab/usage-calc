#!/usr/bin/env python3
"""Export Copilot CLI usage metrics from THIS machine so another machine can
merge them.

Why this file exists
--------------------
The usage dashboard reads `~/.copilot/session-store.db`, which is LOCAL TO ONE
MACHINE. Work done on a project from a second machine is real, it cost real
money, and it is invisible to a dashboard that can only see one store. There
is no network API to ask for it: the store is a local SQLite file and nothing
uploads it.

So this script runs on the OTHER machine and writes a small JSON file per
session. Those files are dropped into the project's contributions directory
and `usage-calc build` merges them.

THIS FILE IS DELIBERATELY STANDALONE. It imports nothing but the standard
library and needs no checkout and no install, because the machine it has to
run on is by definition not the one set up for this work. Fetch it, run it,
copy the output back.

What it does NOT contain
------------------------
No prompt text, no responses, no file contents, no summaries, no turn labels.
Only counts, timestamps, durations, model names and prices. A contribution
file can be read end to end by anyone and reveals what was spent, not what was
said. That is deliberate: a tool that publishes cost data should not quietly
carry conversation text along with it.

Usage
-----
    python export_session.py --list
    python export_session.py --all --out .

`--all` writes every session that has usage events, one file each. Pick
individual ones with `--session <id>` or `--project <substring of cwd/repo>`.

Zero dependencies, standard library only, Python 3.8+. It never writes to the
store and copies it before reading, so a running CLI cannot change it midway.
"""

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import tempfile

FORMAT = "usage-calc-contribution"
VERSION = 1
NANO = 1_000_000_000


def die(msg):
    print("EXPORT FAILED: " + msg, file=sys.stderr)
    sys.exit(1)


def default_db():
    return os.path.join(os.path.expanduser("~"), ".copilot", "session-store.db")


def open_snapshot(path):
    """Copy the store and its WAL so a live CLI cannot change it under us."""
    if not os.path.exists(path):
        die("no session store at " + path)
    tmp = tempfile.mkdtemp(prefix="usagexp-")
    for ext in ("", "-wal", "-shm"):
        src = path + ext
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, "session-store.db" + ext))
    con = sqlite3.connect(os.path.join(tmp, "session-store.db"))
    con.row_factory = sqlite3.Row
    return con, tmp


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_zone():
    now = dt.datetime.now().astimezone()
    off = now.utcoffset() or dt.timedelta(0)
    total = int(off.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return {
        "name": now.tzname() or "local",
        "utc_offset": "%s%02d:%02d" % (sign, total // 3600, (total % 3600) // 60),
    }


def slug(text):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "session")).strip("-").lower()
    return s[:60] or "session"


def project_name(row):
    """Prefer the repository, fall back to the last path segment of the cwd.

    A repository name is what a reader of the dashboard recognises. A cwd is a
    detail of one machine's disk, and on the machine this was written for it is
    the only thing recorded for some sessions.
    """
    if row["repository"]:
        return row["repository"].split("/")[-1]
    cwd = (row["cwd"] or "").rstrip("\\/")
    return os.path.basename(cwd) or "unknown"


def load(con, sid):
    rows = con.execute(
        "SELECT * FROM assistant_usage_events WHERE session_id=? ORDER BY created_at, id",
        (sid,),
    ).fetchall()
    out, total = [], 0
    for r in rows:
        raw = r["token_details_json"]
        if not raw:
            die(
                "row %s carries no token_details_json. The columns disagree with "
                "the details on compaction rows, so the details are the only "
                "figure that reconciles. See the usage-calc README, "
                "'Two aggregations that look obvious and are wrong'." % r["id"]
            )
        chans, nano = {}, 0
        for e in json.loads(raw):
            per_token = e["costPerBatch"] // e["batchSize"]
            n = e["tokenCount"] * per_token
            nano += n
            c = chans.setdefault(e["tokenType"], {"tokens": 0, "nano_aiu": 0, "price": per_token})
            c["tokens"] += e["tokenCount"]
            c["nano_aiu"] += n
        stored = r["total_nano_aiu"] or 0
        if nano != stored:
            die(
                "row %s: details sum to %d nano-AIU, row says %d. Refusing to "
                "export a figure that does not reconcile." % (r["id"], nano, stored)
            )
        total += nano
        out.append(
            {
                "at": r["created_at"],
                "ts": parse_ts(r["created_at"]).timestamp(),
                "duration_ms": r["duration_ms"] or 0,
                "model": r["model"],
                "turn": r["turn_index"],
                "sub": 1 if r["agent_id"] else 0,
                "initiator": r["initiator"],
                "reasoning": r["reasoning_tokens"] or 0,
                "nano": nano,
                "chans": chans,
            }
        )
    return out, total


def summarise(events):
    by = {}
    for e in events:
        d = by.setdefault(e["model"], {"model": e["model"], "requests": 0, "nano_aiu": 0})
        d["requests"] += 1
        d["nano_aiu"] += e["nano"]
    return sorted(by.values(), key=lambda d: -d["nano_aiu"])


def channels(events):
    by = {}
    for e in events:
        for kind, c in e["chans"].items():
            d = by.setdefault(kind, {"type": kind, "tokens": 0, "nano_aiu": 0})
            d["tokens"] += c["tokens"]
            d["nano_aiu"] += c["nano_aiu"]
    return sorted(by.values(), key=lambda d: -d["nano_aiu"])


def contribution(sess, events, total, label):
    # Requests are emitted as compact arrays rather than objects. 4,500 of them
    # as objects is a megabyte of repeated key names for no added meaning.
    cols = ["ts", "duration_ms", "nano", "model", "turn", "sub",
            "input", "cache_read", "cache_write", "output", "reasoning"]
    rows = []
    for e in events:
        g = lambda k: e["chans"].get(k, {}).get("tokens", 0)
        rows.append([
            round(e["ts"], 3), e["duration_ms"], e["nano"], e["model"],
            e["turn"], e["sub"],
            g("input"), g("cache_read"), g("cache_write"), g("output"), e["reasoning"],
        ])
    return {
        "format": FORMAT,
        "version": VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine": label,
        "project": project_name(sess),
        "repository": sess["repository"],
        "cwd": sess["cwd"],
        "session_id": sess["id"],
        "zone": local_zone(),
        "contains_prompt_text": False,
        "totals": {
            "requests": len(events),
            "nano_aiu": total,
            "turns": len({e["turn"] for e in events if e["turn"] is not None}),
            "subagent_requests": sum(e["sub"] for e in events),
            "first": min(e["at"] for e in events),
            "last": max(e["at"] for e in events),
        },
        "models": summarise(events),
        "channels": channels(events),
        "columns": cols,
        "requests": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=default_db())
    p.add_argument("--list", action="store_true", help="show sessions and exit")
    p.add_argument("--all", action="store_true", help="export every session with usage")
    p.add_argument("--session", action="append", default=[], help="session id (repeatable)")
    p.add_argument("--project", action="append", default=[],
                   help="substring of the repo or cwd (repeatable)")
    p.add_argument("--out", default=".", help="directory to write into")
    p.add_argument("--label", default=platform.node() or "other-machine",
                   help="a name for this machine, shown on the dashboard")
    args = p.parse_args()

    con, tmp = open_snapshot(args.db)
    try:
        sessions = con.execute("SELECT * FROM sessions").fetchall()
        counts = dict(con.execute(
            "SELECT session_id, COUNT(*) FROM assistant_usage_events GROUP BY session_id"
        ).fetchall())

        if args.list:
            print("%-38s %7s  %-26s %s" % ("session", "reqs", "project", "cwd"))
            for s in sorted(sessions, key=lambda r: counts.get(r["id"], 0), reverse=True):
                print("%-38s %7d  %-26s %s"
                      % (s["id"], counts.get(s["id"], 0), project_name(s), s["cwd"] or "-"))
            return

        want = []
        for s in sessions:
            if not counts.get(s["id"]):
                continue
            hay = " ".join(filter(None, [s["id"], s["repository"], s["cwd"]])).lower()
            if (args.all
                    or s["id"] in args.session
                    or any(q.lower() in hay for q in args.project)):
                want.append(s)
        if not want:
            die("nothing matched. Run with --list to see what this store holds.")

        os.makedirs(args.out, exist_ok=True)
        written = 0
        for s in want:
            events, total = load(con, s["id"])
            if not events:
                continue
            data = contribution(s, events, total, args.label)
            name = "%s__%s.json" % (slug(data["project"]), s["id"][:8])
            path = os.path.join(args.out, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"), sort_keys=False)
            written += 1
            print("wrote %-42s %6d requests  %10.2f AIU  $%8.2f"
                  % (name, len(events), total / NANO, total / NANO / 100.0))
        print("\n%d file(s) in %s" % (written, os.path.abspath(args.out)))
        print("Copy them into the project's contributions directory "
              "(usage/contrib by default) and re-run `usage-calc build`.")
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
