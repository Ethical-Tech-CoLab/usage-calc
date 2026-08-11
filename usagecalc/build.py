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
from .todos import summary as todo_summary

SCHEMA = "copilot-usage/1"
MARKER = "/*USAGE*/"

# Bumped whenever the packaged template changes in a way splice mode cannot
# deliver. Splice deliberately never touches a project's markup, which is what
# lets a project keep its own styling - but it also means a template upgrade
# reaches nobody, and a page that cannot render a panel looks exactly like a
# page that has nothing to show. The version is compared on every build so a
# stale page says so out loud instead.
#
# THIS COUNTS PRESENTATION CHANGES TOO, and v4 is one: the page gained a fluid
# measure and no new payload key. The narrower rule - bump only for a new key -
# was tempting because a merge is manual work and nobody wants to be nagged
# into it for a stylesheet. It was rejected because it makes the module the
# judge of whether a consumer's page is worth improving. The note says what
# changed; the consumer decides whether to take it.
TEMPLATE_VERSION = 4
VERSION_MARK = "usage-calc-template:"

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
    day_rows = daily(events, turns)
    # Coverage is computed against the SAME local day rows the dashboard shows.
    # Deriving a second set of day boundaries here would let the two disagree
    # by a day at every evening edge and nothing would look wrong.
    plan = todo_summary(sid, work_days={r["date"]: r["requests"] for r in day_rows})

    main_label = (sess["repository"]
                  or os.path.basename(str(sess["cwd"]).rstrip("\\/"))
                  or cfg["title"]).split("/")[-1]
    fleet_data = fleet(main_label,
                       {"machine": None, "repository": sess["repository"],
                        "session": sid},
                       events, contribs)
    sibs = siblings(cfg)
    outs = outputs(root)

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
            "rows": day_rows,
        },
        "plan": plan,
        "channels": channels,
        "models": group(events, "model", "model"),
        "initiators": group(events, "initiator", "initiator"),
        "efforts": group(events, "effort", "effort"),
        "agents": agents,
        "turns": turns,
        "counterfactual": counterfactual(events),
        "fleet": fleet_data,
        "siblings": sibs,
        "scopes": _scopes(main_label, day_rows, fleet_data, sibs, outs),
        "energy": energy(len(events)),
        "catalogue": offered_models({e["model"] for e in events}),
        "outputs": outs,
        "not_measured": NOT_MEASURED,
    }


def _scopes(main_label, day_rows, fleet_data, sibs, outs):
    """One list of selectable repositories, so every control agrees.

    The page had two half-scopes before this: a day chart that offered "this
    repository / all repositories", and several panels that were silently one
    or the other with only the prose to tell them apart. Prose is not a
    control. A reader had to know which panel meant which, and "this one" only
    means anything to someone who already knows where the page was generated.

    So the scopes are built ONCE, here, and every panel is handed the same
    list. Each entry declares what it actually has, because the answer differs
    per panel and per repository:

        usage   spend and time - present for every repository that exported
        days    day-by-day rows - same
        plan    the todo list - PRIMARY ONLY. It lives in the session state of
                the machine the session ran on; a contribution file carries no
                todos and inventing them is not an option.
        output  commits, lines, words - full only where there is a checkout.
                Siblings get commits and dates from the API and nothing else.

    A panel that cannot honour a selection says so and shows what it has. It
    must never quietly fall back to the primary repository's numbers under
    another repository's name.
    """
    if not fleet_data:
        return None

    by_project = {}
    for r in fleet_data.get("sources") or []:
        by_project[r["project"]] = r

    sib_by_name = {}
    for r in ((sibs or {}).get("rows") or []):
        sib_by_name[r["name"]] = r

    entries = [{
        "key": "all",
        "label": "All repositories",
        "kind": "all",
        "usage": True,
        "days": bool(fleet_data.get("days")),
        "plan": False,
        # Commits are known for every repository - they come from GitHub, not
        # from a checkout - so a summed commit count is real. Lines, words and
        # files are not, and the panel says which is which rather than adding
        # a number that only covers one repository to a label that says all.
        "output": "commits",
        "requests": (fleet_data.get("totals") or {}).get("requests"),
        "usd": (fleet_data.get("totals") or {}).get("usd"),
    }]

    for name, r in sorted(by_project.items(),
                          key=lambda kv: (not kv[1].get("primary"),
                                          -kv[1].get("nano_aiu", 0))):
        sib = sib_by_name.get(name)
        primary = bool(r.get("primary"))
        entries.append({
            "key": name,
            "label": name + (" (main)" if primary else ""),
            "kind": "main" if primary else "sibling",
            "usage": True,
            "days": bool(r.get("day_rows")),
            "plan": primary,
            # Only the repository this page was generated in has a working
            # tree to count. Everything else gets what GitHub will answer.
            "output": "full" if primary else ("commits" if sib else False),
            "requests": r.get("requests"),
            "usd": r.get("usd"),
            "machine": r.get("machine"),
            "commits": (outs or {}).get("commit_count") if primary
                       else (sib or {}).get("commits"),
            "first_commit": (sib or {}).get("first_commit") if not primary else None,
            "last_commit": (sib or {}).get("last_commit") if not primary else None,
        })

    # A repository named as a sibling that never exported its usage still
    # belongs in the list. Leaving it out would make the selector agree with
    # the totals and disagree with the project.
    for name, sib in sorted(sib_by_name.items()):
        if name in by_project:
            continue
        entries.append({
            "key": name, "label": name, "kind": "sibling",
            "usage": False, "days": False, "plan": False,
            "output": "commits", "requests": None, "usd": None,
            "commits": sib.get("commits"),
            "first_commit": sib.get("first_commit"),
            "last_commit": sib.get("last_commit"),
        })

    known = [e.get("commits") for e in entries[1:] if e.get("commits")]
    entries[0]["commits"] = sum(known) if known else None
    entries[0]["commits_partial"] = any(
        not e.get("commits") for e in entries[1:])

    return {
        "main": main_label,
        "default": "all",
        "entries": entries,
        "note": (
            "Selecting one repository shows that repository's own stream. Cost "
            "and requests add up across selections; TIME DOES NOT. Engaged time "
            "is cut into sittings over whichever stream is selected, so a pause "
            "spent in a sibling repository reads as idle in one and as work in "
            "the other. Only the merged view cuts the pooled stream, which is "
            "the reading that matches one person."
        ),
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
    _warn_if_stale(html, path)
    html = _splice(html, data, os.path.basename(path))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return path


def page_version(html):
    """Which generation of the packaged template a page was made from.

    A page written before versioning existed has no mark at all, which is
    version 1 by definition rather than an error.
    """
    i = html.find(VERSION_MARK)
    if i < 0:
        return 1
    digits = ""
    for ch in html[i + len(VERSION_MARK):]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 1


def _warn_if_stale(html, path):
    v = page_version(html)
    if v >= TEMPLATE_VERSION:
        return
    print("  NOTE: %s was built from template v%d; the installed template is v%d."
          % (os.path.basename(path), v, TEMPLATE_VERSION))
    print("        The numbers below are current. Panels added since v%d will not"
          % v)
    print("        appear until the new markup is carried across - `usage-calc")
    print("        build --fresh` takes the packaged page, or diff the template")
    print("        at: %s" % template_path())


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
