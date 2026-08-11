"""Assemble the whole payload, and write it into the template.

This is the top of the package: it reads the store, derives everything, and
returns one JSON-serialisable dict. The dashboard is that dict plus a template
that renders it - which is why the same payload can be consumed by anything
else without going near the HTML.
"""

import datetime as dt
import json
import os
import shutil

from .contrib import fleet, read_contributions
from .energy import energy
from .intervals import busy_union
from .metrics import (CHANNELS, IDLE_CUTOFFS, active_time, counterfactual, daily,
                      group, local_zone)
from .project import dates, load_config, offered_models, outputs, siblings
from .store import (NANO_PER_AIU, StoreError, load_events, open_snapshot,
                    pick_session, tok, turn_labels, usd)

SCHEMA = "copilot-usage/1"
MARKER = "/*USAGE*/"

NOT_MEASURED = [
    ["Energy", "No joules are recorded anywhere in this data. The energy panel "
     "applies a published per-query figure to a request count. It is an "
     "estimate stapled to a fact, and the two ends of its own bracket differ "
     "by a factor of 24."],
    ["Water and embodied carbon", "Downstream of energy, and not derivable "
     "from it without a site-specific PUE and WUE."],
    ["What was actually paid", "The store prices tokens. A Copilot "
     "subscription bills premium requests. The two are not the same number "
     "and this data cannot bridge them."],
    ["Human time", "No keystroke or focus telemetry exists here. The "
     "active-time figures are inferred from gaps between requests."],
    ["Value", "Nothing here measures whether any of the output was worth "
     "having."],
]

PERSON_NOTE = (
    "Person time is a RESIDUAL, not a measurement. No keystroke or focus "
    "telemetry exists in this store. Within a sitting either a model was "
    "working or it was not, so engaged minus model is the time somebody could "
    "have been reading, typing or thinking - and it counts the same if they "
    "walked away. It also misses the reading done after a sitting's last "
    "request, so it errs in both directions rather than bounding the truth on "
    "one side."
)


def build(root=None, db=None, session=None, cwd=None, config=None):
    """Read the store and return the full payload dict."""
    cfg = config or load_config(root or os.getcwd())
    root = cfg["root"]

    con, tmp = open_snapshot(db)
    try:
        sess = pick_session(con, cwd=cwd or root, session=session)
        sid = sess["id"]
        events = load_events(con, sid)
        labels = turn_labels(con, sid) if cfg.get("turn_labels", True) else {}
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)

    contrib_dir = os.path.join(root, cfg["contrib_dir"])
    contribs = read_contributions(contrib_dir, primary_sid=sid)

    channels = _channels(events)
    total_nano = sum(e["nano"] for e in events)
    ts = sorted(e["ts"] for e in events)
    union_s, blocks = busy_union(events)
    turns = _turns(events, labels)
    agents = _agents(events)

    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                          .isoformat().replace("+00:00", "Z"),
        "generator": "usage-calc",
        "project": {
            "name": cfg["title"],
            "cwd": sess["cwd"],
            "repository": sess["repository"],
            "branch": sess["branch"],
        },
        "session": {
            "id": sid,
            "created_at": sess["created_at"],
            "updated_at": sess["updated_at"],
        },
        "units": {
            "aiu_is": "one US cent",
            "nano_aiu_per_aiu": NANO_PER_AIU,
            "note": "AIU prices in the store match the per-model prices the "
                    "client publishes in models.json, which in turn match "
                    "vendor list prices to the cent. See the README.",
        },
        "totals": {
            "requests": len(events),
            "turns": len({e["turn"] for e in events}),
            "models": len({e["model"] for e in events}),
            "subagents": len({e["agent_id"] for e in events if e["agent_id"]}),
            "subagent_requests": sum(1 for e in events if e["agent_id"]),
            "nano_aiu": total_nano,
            "aiu": round(total_nano / NANO_PER_AIU, 3),
            "usd": round(usd(total_nano), 2),
            "tokens": dict(
                {k: sum(tok(e, k) for e in events) for k in CHANNELS},
                reasoning=sum(e["reasoning"] for e in events)),
            "premium_requests": sum(1 for e in events if e["initiator"] == "user"),
            "multipliers": sorted({e["multiplier"] for e in events if e["multiplier"]}),
        },
        "time": {
            "first_request": events[0]["at"],
            "last_request": events[-1]["at"],
            "wall_span_s": round(ts[-1] - ts[0], 1),
            "inference_sum_s": round(sum(e["duration_ms"] for e in events) / 1000.0, 1),
            "inference_union_s": round(union_s, 1),
            "busy_blocks": blocks,
            "active": active_time(events),
        },
        "dates": dates(root, events),
        "days": {
            "zone": local_zone(),
            "cutoffs": list(IDLE_CUTOFFS),
            "default_cutoff_s": 300,
            "person_note": PERSON_NOTE,
            "rows": daily(events, turns),
        },
        "channels": channels,
        "models": group(events, "model", "model"),
        "initiators": group(events, "initiator", "initiator"),
        "efforts": group(events, "effort", "effort"),
        "agents": agents,
        "turns": turns,
        "counterfactual": counterfactual(events),
        "fleet": fleet(
            (sess["repository"]
             or os.path.basename(str(sess["cwd"]).rstrip("\\/"))
             or cfg["title"]).split("/")[-1],
            {"machine": None, "repository": sess["repository"], "session": sid},
            events, contribs),
        "siblings": siblings(cfg),
        "energy": energy(len(events)),
        "catalogue": offered_models({e["model"] for e in events}),
        "outputs": outputs(root),
        "not_measured": NOT_MEASURED,
    }


def _channels(events):
    chans = {}
    for e in events:
        for kind, ch in e["chans"].items():
            k = (e["model"], kind, ch["price_nano_per_token"])
            d = chans.setdefault(k, {
                "model": e["model"], "type": kind,
                "price_nano_per_token": ch["price_nano_per_token"],
                "tokens": 0, "nano_aiu": 0,
            })
            d["tokens"] += ch["tokens"]
            d["nano_aiu"] += ch["nano_aiu"]
    out = sorted(chans.values(), key=lambda d: -d["nano_aiu"])
    for c in out:
        c["usd"] = round(usd(c["nano_aiu"]), 4)
        c["usd_per_1m"] = round(
            c["price_nano_per_token"] * 1e6 / NANO_PER_AIU / 100.0, 4)
    return out


def _turns(events, labels):
    turns = []
    for t in sorted({e["turn"] for e in events}, key=lambda x: (x is None, x)):
        sub = [e for e in events if e["turn"] == t]
        row = {
            "index": t,
            "label": labels.get(t, ""),
            "started": min(e["at"] for e in sub),
            "requests": len(sub),
            "nano_aiu": sum(e["nano"] for e in sub),
            "usd": round(usd(sum(e["nano"] for e in sub)), 3),
            "duration_ms": sum(e["duration_ms"] for e in sub),
            "reasoning": sum(e["reasoning"] for e in sub),
            "subagent_requests": sum(1 for e in sub if e["agent_id"]),
        }
        for k in CHANNELS:
            row[k] = sum(tok(e, k) for e in sub)
        turns.append(row)
    return turns


def _agents(events):
    agents = [a for a in group(events, "agent_id", "agent") if a["agent"]]
    for a in agents:
        same = [e for e in events if e["agent_id"] == a["agent"]]
        a["started"] = min(e["at"] for e in same)
        a["turn"] = same[0]["turn"]
        a["models"] = sorted({e["model"] for e in same})
    return agents


def template_path():
    return os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")


def render(data, out_html, template=None):
    """Write a standalone dashboard: the template with this payload inside it."""
    src = template or template_path()
    with open(src, encoding="utf-8") as fh:
        html = fh.read()
    html = _splice(html, data, os.path.basename(src))
    d = os.path.dirname(os.path.abspath(out_html))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(out_html, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return out_html


def inject(path, data):
    """Rewrite the payload inside an EXISTING dashboard, leaving all else alone.

    This is what a project uses once it has styled its own copy of the
    template: regenerating the numbers must not discard the styling.
    """
    if not os.path.exists(path):
        print("  no dashboard at %s, skipping injection" % path)
        return None
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    html = _splice(html, data, os.path.basename(path))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return path


def _splice(html, data, name):
    # A page that mentions its own marker in prose carries three of them, and a
    # non-greedy splice then silently deletes everything between the prose and
    # the script - rendering an empty page with no console error. Count first.
    if html.count(MARKER) != 2:
        raise StoreError("expected exactly two %s markers in %s, found %d"
                         % (MARKER, name, html.count(MARKER)))
    a = html.index(MARKER) + len(MARKER)
    b = html.index(MARKER, a)
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    return html[:a] + payload + html[b:]
