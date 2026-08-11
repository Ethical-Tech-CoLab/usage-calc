"""The plan the session kept for itself, read back as coverage - not as a score.

The CLI keeps a SECOND SQLite file per session, separate from the billing
store:

    ~/.copilot/session-state/<session-id>/session.db

It holds the working todo list - `todos` and `todo_deps`. It is keyed by the
same session id the billing store uses, so the two join cleanly, and that join
is the only reason this module is worth having: on its own the todo list says
almost nothing, and against the billed requests it says something specific.

WHY THERE IS NO COMPLETION RATE IN HERE

The obvious number is "153 of 153 done, 100%". It is worthless, and shipping it
would have been the worst kind of telemetry - a metric that reads 100% for
everybody, forever, and therefore distinguishes nothing.

It reads 100% because of how the list is used, not because of how the work
went. A session closes its todos as it goes, so at any moment you are looking
at a list that has been tidied. Nothing that was abandoned is still sitting
there marked `pending` to drag the number down; it was either closed or never
written. The rate measures the tidying.

WHAT IS ACTUALLY MEASURABLE, AND THE TWO HONEST CAVEATS

1. COVERAGE. Which days that cost money also had any planning on them. This one
   is real, it varies, and it can embarrass you - in the session this module
   was written for, two working days carrying 725 billed requests had no todos
   written at all. That is a fact about how the work was run and it is exactly
   the sort of thing a usage dashboard should be willing to say.

2. HOW LONG A TODO STAYED OPEN is mostly NOT measurable, and the data says so
   itself. `updated_at` defaults to `created_at`, so a row inserted and closed
   without an intervening status change carries a zero-second lifetime that
   means "never observed in progress", not "done instantly". In the reference
   session that is 119 of 153 rows. So the lifetime figures are reported over
   the measurable subset only, with the size of that subset alongside them, and
   never as an average over everything.

NO TODO TEXT LEAVES THIS MODULE.

Titles and descriptions are prose a person wrote about unreleased work, which
puts them in the same class as prompt text - and `turn_labels` is deliberately
never exported for exactly that reason. `summary()` returns counts, dates and
ratios. `titles()` exists for local terminal use and is not called by the
payload builder.
"""

import datetime as dt
import os
import sqlite3

STATE_DIR = os.path.join(os.path.expanduser("~"), ".copilot", "session-state")

STATUSES = ("pending", "in_progress", "done", "blocked")


def state_dir():
    """Where per-session state lives, unless COPILOT_SESSION_STATE says otherwise."""
    return os.environ.get("COPILOT_SESSION_STATE") or STATE_DIR


def db_path(session_id, root=None):
    return os.path.join(root or state_dir(), session_id, "session.db")


def _connect(path):
    # Read-only: this file belongs to a live CLI, and a stray write to someone's
    # working todo list would be an unforgivable side effect of a metrics run.
    uri = "file:%s?mode=ro" % path.replace("\\", "/")
    return sqlite3.connect(uri, uri=True)


def _has_todos(con):
    row = con.execute(
        "select count(*) from sqlite_master where type='table' and name='todos'"
    ).fetchone()
    return bool(row and row[0])


def _utc_to_local_day(s):
    """`datetime('now')` writes UTC with no zone marker; days are shown local.

    The dashboard cuts its day rows at LOCAL midnight. Comparing those against
    raw UTC date strings would misfile every todo written in the local evening,
    which for a UTC-4 timezone is a large share of them.
    """
    if not s:
        return None
    try:
        t = dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return t.replace(tzinfo=dt.timezone.utc).astimezone().date().isoformat()


def _seconds(a, b):
    try:
        t0 = dt.datetime.strptime(a[:19], "%Y-%m-%d %H:%M:%S")
        t1 = dt.datetime.strptime(b[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return (t1 - t0).total_seconds()


def read(session_id, root=None):
    """Rows and dependency counts for one session.

    Always returns a (rows, deps) pair. An earlier draft returned a bare [] on
    the missing-file path and a tuple otherwise, which is the same shape-drift
    bug that bit two other functions in this package during the port: it type-
    checks fine at every call site until the day a session has no list.
    """
    empty = ([], {"edges": 0, "todos": 0})
    path = db_path(session_id, root)
    if not os.path.exists(path):
        return empty
    con = _connect(path)
    try:
        if not _has_todos(con):
            return empty
        rows = con.execute(
            "select id, status, created_at, updated_at from todos"
        ).fetchall()
        deps = con.execute("select count(*), count(distinct todo_id) from todo_deps").fetchone()
    finally:
        con.close()
    out = []
    for tid, status, created, updated in rows:
        out.append({
            "id": tid,
            "status": status,
            "created_at": created,
            "updated_at": updated,
            "day": _utc_to_local_day(created),
            "open_s": _seconds(created, updated),
        })
    out.sort(key=lambda r: r["created_at"] or "")
    return out, {"edges": deps[0] if deps else 0, "todos": deps[1] if deps else 0}


def summary(session_id, work_days=None, root=None):
    """Structural summary. No titles, no descriptions, ever.

    `work_days` is {local-day: requests} from the billing store. Pass it and
    coverage is computed; leave it out and the coverage block is omitted rather
    than guessed at, because coverage against an assumed set of working days
    would be the metric quietly measuring nothing again.
    """
    rows, deps = read(session_id, root)
    if not rows:
        return None

    by_status = {s: 0 for s in STATUSES}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    # Lifetimes are only meaningful where the row was actually touched again.
    measurable = sorted(r["open_s"] for r in rows
                        if r["status"] == "done" and r["open_s"] is not None
                        and r["open_s"] >= 1)
    same_second = sum(1 for r in rows
                      if r["open_s"] is not None and r["open_s"] < 1)

    per_day = {}
    for r in rows:
        if r["day"]:
            per_day[r["day"]] = per_day.get(r["day"], 0) + 1

    out = {
        "total": len(rows),
        "by_status": by_status,
        "deps": deps,
        "days": [{"day": d, "written": per_day[d]} for d in sorted(per_day)],
        "lifetime": {
            "measurable": len(measurable),
            "unmeasurable": len(rows) - len(measurable),
            "same_second": same_second,
            "median_s": round(measurable[len(measurable) // 2], 1) if measurable else None,
            "max_s": round(measurable[-1], 1) if measurable else None,
            "note": "A row whose updated_at still equals its created_at was "
                    "never observed in progress. That is missing data, not a "
                    "zero-second todo, so it is excluded rather than averaged in.",
        },
        "note": "Completion rate is deliberately not reported. A session closes "
                "its list as it goes, so the rate measures tidying and reads "
                "100% for everyone.",
    }

    if work_days:
        planned = {d: n for d, n in work_days.items() if d in per_day}
        billed = sum(work_days.values())
        covered = sum(planned.values())
        out["coverage"] = {
            "work_days": len(work_days),
            "planned_days": len(planned),
            "unplanned_days": sorted(d for d in work_days if d not in per_day),
            "requests_total": billed,
            "requests_planned": covered,
            "pct": round(100.0 * covered / billed, 1) if billed else None,
            "note": "Share of billed requests made on a day that had any todo "
                    "written. Low is not automatically bad - some days are one "
                    "long obvious task - but it is where unplanned spend hides.",
        }
    return out


def titles(session_id, root=None, limit=None):
    """Titles, for a terminal the person is sitting at. NOT part of the payload."""
    path = db_path(session_id, root)
    if not os.path.exists(path):
        return []
    con = _connect(path)
    try:
        if not _has_todos(con):
            return []
        q = ("select id, status, title, created_at, updated_at "
             "from todos order by created_at")
        if limit:
            q += " limit %d" % int(limit)
        return [{"id": a, "status": b, "title": c, "created_at": d, "updated_at": e}
                for a, b, c, d, e in con.execute(q)]
    finally:
        con.close()


def sessions_with_todos(root=None):
    """Every session on this machine that kept a list, newest activity first."""
    base = root or state_dir()
    if not os.path.isdir(base):
        return []
    out = []
    for sid in os.listdir(base):
        path = db_path(sid, base)
        if not os.path.exists(path):
            continue
        try:
            con = _connect(path)
        except sqlite3.Error:
            continue
        try:
            if not _has_todos(con):
                continue
            n, last = con.execute(
                "select count(*), max(updated_at) from todos").fetchone()
        except sqlite3.Error:
            continue
        finally:
            con.close()
        if n:
            out.append({"session": sid, "todos": n, "last": last})
    out.sort(key=lambda r: r["last"] or "", reverse=True)
    return out
