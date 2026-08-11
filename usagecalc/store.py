"""Reading the Copilot CLI session store.

The store is a local SQLite file, written by the client as it works. Nothing
has to be collected in advance and nothing uploads it, which has two
consequences that shape this whole package: the data is unusually good (one
row per request, with the price it was billed at), and it is PER MACHINE.

Two things in here are less obvious than they look.

COST IS READ FROM `token_details_json`, NEVER FROM THE TOKEN COLUMNS. The
details are a list of per-channel entries, each carrying its own rate, and
they reconcile exactly to the recorded cost. The flat columns disagree with
them on compaction rows. A reader that trusts the columns silently
under-counts, so this module refuses to run rather than guess: a row with no
details is a hard error, and details that do not sum to the row's own total
are a hard error too. Both have caught real problems.

THE TIME COLUMN IS `created_at`, NOT `timestamp`. It is an ISO-8601 string
with a trailing Z.
"""

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

NANO_PER_AIU = 1_000_000_000
# GitHub's billing documentation states 1 AI credit = $0.01 USD. What is still
# inferred - and this is the assumption to attack first - is that the
# total_nano_aiu column in a local CLI store is denominated in that same
# documented credit.
CENTS_PER_AIU = 1.0

# Turns whose text is injected context rather than something a person typed.
LABEL_SKIP = re.compile(r"^\s*<(skill-context|system|environment)", re.I)


class StoreError(RuntimeError):
    """Raised when the store cannot be trusted to produce an honest number."""


def die(msg):
    raise StoreError(msg)


def default_db():
    """Where the CLI keeps its store, unless COPILOT_SESSION_STORE says otherwise."""
    env = os.environ.get("COPILOT_SESSION_STORE")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".copilot", "session-store.db")


def open_snapshot(path=None):
    """Copy the store, and its WAL, so a live CLI cannot change it under us.

    Returns (connection, tempdir). The caller may delete the tempdir; leaving
    it is harmless and the OS will clear it.
    """
    path = path or default_db()
    if not os.path.exists(path):
        die("no session store at %s. Set COPILOT_SESSION_STORE if it lives "
            "elsewhere." % path)
    tmp = tempfile.mkdtemp(prefix="usagecalc-")
    for ext in ("", "-wal", "-shm"):
        src = path + ext
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, "session-store.db" + ext))
    con = sqlite3.connect(os.path.join(tmp, "session-store.db"))
    con.row_factory = sqlite3.Row
    return con, tmp


def list_sessions(con):
    """Every session in the store, with its request count. Newest last."""
    rows = con.execute(
        "SELECT id, cwd, repository, branch, created_at, updated_at FROM sessions"
    ).fetchall()
    counts = dict(con.execute(
        "SELECT session_id, COUNT(*) FROM assistant_usage_events GROUP BY session_id"
    ).fetchall())
    out = []
    for r in sorted(rows, key=lambda r: r["created_at"] or ""):
        d = dict(r)
        d["requests"] = counts.get(r["id"], 0)
        out.append(d)
    return out


def pick_session(con, cwd=None, session=None):
    """Find the session for a working directory, or a specific session id.

    Where several sessions share a directory the one with the MOST REQUESTS
    wins, so a stray one-shot session cannot shadow the real body of work.
    """
    rows = con.execute(
        "SELECT id, cwd, repository, branch, created_at, updated_at FROM sessions"
    ).fetchall()
    counts = dict(con.execute(
        "SELECT session_id, COUNT(*) FROM assistant_usage_events GROUP BY session_id"
    ).fetchall())
    if session:
        hit = [r for r in rows if r["id"] == session]
        if not hit:
            die("no session " + session)
        return hit[0]
    want = os.path.normcase(os.path.abspath(cwd or os.getcwd()))
    hit = [r for r in rows
           if r["cwd"] and os.path.normcase(os.path.abspath(r["cwd"])) == want]
    if not hit:
        die("no session recorded for %s.\n"
            "Run `usage-calc sessions` to see what the store holds." % want)
    hit.sort(key=lambda r: counts.get(r["id"], 0), reverse=True)
    return hit[0]


def parse_ts(s):
    """The store writes ISO-8601 with a trailing Z. Return a tz-aware datetime."""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_events(con, sid, strict=True):
    """One dict per request, with its channel prices resolved.

    `strict` exists for the query tool, which reads sessions it did not build
    and should report what it finds rather than refuse. The dashboard
    generator always runs strict: a total it cannot reconcile is worse than no
    total, because it looks exactly like one that reconciles.
    """
    rows = con.execute(
        "SELECT * FROM assistant_usage_events WHERE session_id=? ORDER BY created_at, id",
        (sid,),
    ).fetchall()
    if not rows and strict:
        die("session %s has no usage events" % sid)
    events = []
    for r in rows:
        raw = r["token_details_json"]
        if not raw:
            if strict:
                die("row %s carries no token_details_json; totals would be a guess"
                    % r["id"])
            chans, nano = _chans_from_columns(r), r["total_nano_aiu"] or 0
        else:
            details = json.loads(raw)
            chans, nano = {}, 0
            for e in details:
                per_token = e["costPerBatch"] // e["batchSize"]
                n = e["tokenCount"] * per_token
                nano += n
                chans[e["tokenType"]] = {
                    "tokens": e["tokenCount"],
                    "price_nano_per_token": per_token,
                    "nano_aiu": n,
                }
            stored = r["total_nano_aiu"] or 0
            if nano != stored:
                if strict:
                    die("row %s: token_details_json sums to %d nano-AIU but the "
                        "row says %d. The reconciliation this package depends on "
                        "has broken." % (r["id"], nano, stored))
                nano = stored
        events.append({
            "id": r["id"],
            "turn": r["turn_index"],
            "model": r["model"],
            "agent_id": r["agent_id"],
            "initiator": r["initiator"],
            "endpoint": r["api_endpoint"],
            "effort": r["reasoning_effort"],
            "finish": r["finish_reason"],
            "multiplier": r["request_multiplier"],
            "reasoning": r["reasoning_tokens"] or 0,
            "duration_ms": r["duration_ms"] or 0,
            "ttft_ms": r["time_to_first_token_ms"],
            "at": r["created_at"],
            "ts": parse_ts(r["created_at"]).timestamp(),
            "chans": chans,
            "nano": nano,
        })
    return events


def _chans_from_columns(r):
    """Last resort for non-strict reads: the flat columns, which can disagree."""
    out = {}
    for key, col in (("input", "input_tokens"), ("output", "output_tokens"),
                     ("cache_read", "cache_read_tokens"),
                     ("cache_write", "cache_write_tokens")):
        out[key] = {"tokens": r[col] or 0, "price_nano_per_token": 0, "nano_aiu": 0}
    return out


def turn_labels(con, sid, limit=96):
    """First line of each user message, keyed by turn index.

    THIS IS THE ONLY FUNCTION IN THE PACKAGE THAT TOUCHES PROMPT TEXT, and it
    is never included in an export. A project that would rather publish none
    of it can pass labels=False to the builder.

    Turns whose "user message" is actually injected context - a skill block,
    a system note, an environment dump - are labelled as such rather than
    quoted, because presenting machine-generated text as something a person
    typed misrepresents who asked for what.
    """
    out = {}
    try:
        rows = con.execute(
            "SELECT turn_index, user_message FROM turns WHERE session_id=?", (sid,)
        ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        msg = (r["user_message"] or "").strip()
        if not msg or LABEL_SKIP.match(msg):
            out[r["turn_index"]] = "(tool or skill context, not a typed request)"
            continue
        one = " ".join(msg.split())
        out[r["turn_index"]] = one[:limit] + ("..." if len(one) > limit else "")
    return out


def tok(ev, kind):
    return ev["chans"].get(kind, {}).get("tokens", 0)


def usd(nano):
    """List-price equivalent in dollars. NOT a bill - see the README."""
    return nano / NANO_PER_AIU * CENTS_PER_AIU / 100.0
