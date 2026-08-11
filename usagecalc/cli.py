"""The command line: build, export, query, report, verify, sessions, init.

Every subcommand is a thin wrapper. The reasoning lives in the modules; this
file only decides what to call and what to print.
"""

import argparse
import json
import os
import runpy
import subprocess
import sys

from . import __version__
from .build import build as build_payload, inject, render, template_path
from .project import CONFIG_NAME, DEFAULTS, find_root, load_config
from .store import StoreError, list_sessions, default_db, open_snapshot

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools")


def cmd_init(args):
    root = find_root(args.root)
    path = os.path.join(root, CONFIG_NAME)
    if os.path.exists(path) and not args.force:
        print("%s already exists; pass --force to overwrite" % path)
        return 1
    # Derive what can be derived, so the file a user opens is already true
    # rather than a form of nulls they have to work out how to fill.
    derived = load_config(root)
    cfg = {k: v for k, v in DEFAULTS.items()}
    cfg["title"] = derived["title"]
    cfg["owner"] = derived["owner"]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    print("wrote %s" % path)
    if cfg["owner"]:
        print("  owner  %s (from remote.origin.url)" % cfg["owner"])
    print("Edit `siblings` if other repositories are part of the same project "
          "but were worked on from another machine. Their usage cannot be seen "
          "from here, and naming them is what stops the total looking complete "
          "when it is a floor.")
    return 0


def cmd_sessions(args):
    con, tmp = open_snapshot(args.db)
    try:
        rows = list_sessions(con)
    finally:
        con.close()
    print("%-38s %7s  %-30s %s" % ("session", "reqs", "repository", "cwd"))
    for r in rows:
        print("%-38s %7d  %-30s %s"
              % (r["id"], r["requests"], r["repository"] or "-", r["cwd"] or "-"))
    return 0


def cmd_build(args):
    root = find_root(args.root)
    cfg = load_config(root)
    data = build_payload(root=root, db=args.db, session=args.session,
                         cwd=args.cwd, config=cfg)

    out_json = args.out or os.path.join(root, cfg["out_json"])
    d = os.path.dirname(os.path.abspath(out_json))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(out_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1)
        fh.write("\n")

    t = data["totals"]
    print("session %s" % data["session"]["id"])
    print("  %d requests over %d turns, %d models, %d sub-agents"
          % (t["requests"], t["turns"], t["models"], t["subagents"]))
    print("  %s AIU = $%s at list price" % (t["aiu"], t["usd"]))
    print("  tokens: %d cache-read, %d cache-write, %d output, %d fresh input"
          % (t["tokens"]["cache_read"], t["tokens"]["cache_write"],
             t["tokens"]["output"], t["tokens"]["input"]))
    print("  inference %.2f h summed, %.2f h union; wall span %.2f h"
          % (data["time"]["inference_sum_s"] / 3600,
             data["time"]["inference_union_s"] / 3600,
             data["time"]["wall_span_s"] / 3600))
    if data["fleet"]:
        f = data["fleet"]["totals"]
        print("  merged across %d projects on %d machines: %d requests, $%s"
              % (f["projects"], f["machines"], f["requests"], f["usd"]))
    print("  wrote %s" % out_json)

    if args.no_html:
        return 0
    out_html = args.dashboard or os.path.join(root, cfg["out_html"])
    if os.path.exists(out_html) and not args.fresh:
        # The project may have restyled its copy. Regenerating the numbers must
        # not throw that away, so an existing page is spliced, not replaced.
        inject(out_html, data)
        print("  injected into %s" % out_html)
    else:
        render(data, out_html)
        print("  rendered %s from %s" % (out_html, template_path()))
    return 0


def cmd_export(args):
    return _run_tool("export_session.py", args.rest)


def cmd_query(args):
    return _run_tool("query_sessions.py", args.rest)


def cmd_report(args):
    return _run_tool("report_fleet.py", args.rest)


def cmd_verify(args):
    root = find_root(args.root)
    cfg = load_config(root)
    page = args.page or os.path.join(root, cfg["out_html"])
    js = os.path.join(TOOLS, "verify_usage.js")
    try:
        return subprocess.call(["node", js, page])
    except FileNotFoundError:
        print("node was not found on PATH. The verifier drives a real browser "
              "through Playwright; the Python side of the package needs "
              "neither.")
        return 2


def _run_tool(name, rest):
    path = os.path.join(TOOLS, name)
    sys.argv = [path] + list(rest)
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as e:
        return e.code or 0
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="usage-calc",
        description="What a Copilot CLI project actually cost, measured not guessed.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write a usage-calc.json into a project")
    p.add_argument("--root")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("sessions", help="list what the local store holds")
    p.add_argument("--db", default=None)
    p.set_defaults(fn=cmd_sessions)

    p = sub.add_parser("build", help="generate the data and the dashboard")
    p.add_argument("--root")
    p.add_argument("--db", default=None)
    p.add_argument("--session", help="read a specific session id")
    p.add_argument("--cwd", help="read the session recorded for this directory")
    p.add_argument("--out", help="where to write the JSON payload")
    p.add_argument("--dashboard", help="where to write or update the HTML")
    p.add_argument("--fresh", action="store_true",
                   help="overwrite the dashboard from the packaged template, "
                        "discarding any local styling")
    p.add_argument("--no-html", action="store_true", help="JSON only")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("export", help="export this machine's usage for another to merge")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("query", help="what has this machine been doing lately")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_query)

    p = sub.add_parser("report", help="print merged contributions without building a page")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("verify", help="drive the dashboard in a browser and check it")
    p.add_argument("--root")
    p.add_argument("--page")
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    if getattr(args, "db", None) is None and hasattr(args, "db"):
        args.db = default_db()
    try:
        return args.fn(args)
    except StoreError as e:
        print("usage-calc: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
