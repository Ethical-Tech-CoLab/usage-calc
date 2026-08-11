"""Everything that is a property of A PROJECT rather than of the store.

This is the module a consuming project configures. Nothing else in the package
knows a repository name, an owner, an output path or a title - which is what
makes the rest of it portable.

The configuration is a small JSON file, `usage-calc.json`, at the root of the
project being measured. Every key is optional; the defaults are derived from
the git repository the file sits in.
"""

import datetime as dt
import json
import os
import subprocess

from .store import parse_ts

CONFIG_NAME = "usage-calc.json"

DEFAULTS = {
    # Shown in the dashboard heading. Defaults to the repository name.
    "title": None,
    # Where the dashboard and its JSON are written, relative to the project root.
    "out_html": "usage/usage-dashboard.html",
    "out_json": "usage/usage-data.json",
    # Where exports from other machines are dropped.
    "contrib_dir": "usage/contrib",
    # Other repositories that are part of the same project but whose usage
    # lives in another machine's store. Names only; owner is separate.
    "siblings": [],
    "owner": None,
    # Include the first line of each user message in the output?
    "turn_labels": True,
}


def find_root(start=None):
    """The git top level containing `start`, or `start` itself."""
    start = os.path.abspath(start or os.getcwd())
    try:
        out = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except Exception:
        pass
    return start


def load_config(root):
    """Read usage-calc.json if present, filling in defaults."""
    cfg = dict(DEFAULTS)
    path = os.path.join(root, CONFIG_NAME)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    cfg["root"] = root
    if not cfg.get("title"):
        cfg["title"] = os.path.basename(root)
    if not cfg.get("owner"):
        cfg["owner"] = _owner_from_remote(root)
    cfg["config_file"] = path if os.path.exists(path) else None
    return cfg


def _owner_from_remote(root):
    url = git(root, "config", "--get", "remote.origin.url")
    if not url:
        return None
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.replace(":", "/").split("/")
    return parts[-2] if len(parts) >= 2 else None


def git(root, *args):
    try:
        return subprocess.run(["git"] + list(args), cwd=root, capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def dates(root, events):
    """When the work started, and when it was last touched.

    THESE ARE TWO DIFFERENT QUESTIONS WITH DIFFERENT ANSWERS. The last request
    is when a model last ran; the last commit is when the published work last
    changed. A reader asking "is this current" means the second. Both are
    reported, because quoting either alone misleads - a session can burn
    requests without publishing anything, and a commit can land long after the
    reasoning behind it.
    """
    first = parse_ts(events[0]["at"]).astimezone()
    last = parse_ts(events[-1]["at"]).astimezone()
    commit = git(root, "log", "-1", "--pretty=format:%aI")
    first_commit = git(root, "log", "--reverse", "--pretty=format:%aI")
    first_commit = first_commit.splitlines()[0] if first_commit else ""
    return {
        "started": first.isoformat(),
        "started_source": "first request issued in this session",
        "last_request": last.isoformat(),
        "last_commit": commit,
        "first_commit": first_commit,
        "calendar_days": (last.date() - first.date()).days + 1,
        "active_days": len({
            dt.datetime.fromtimestamp(e["ts"]).astimezone().date() for e in events
        }),
        "generated": dt.datetime.now().astimezone().isoformat(),
    }


def siblings(cfg):
    """Repositories that are part of the project but invisible to this store.

    Their cost is real and no dashboard generated here can reach it: the store
    is a local SQLite file, per machine, with no API to query. Rather than let
    that silently shrink the published total, the repositories are NAMED, what
    CAN be seen from here is fetched (commits and dates, which live on GitHub),
    and the part that cannot is labelled as missing.

    A STATED HOLE IS EVIDENCE; AN UNSTATED ONE IS AN UNDERSTATEMENT.

    Network failure is not fatal - the names and the caveat still publish.
    Returns None when the project declares no siblings.
    """
    names = cfg.get("siblings") or []
    if not names:
        return None
    owner = cfg.get("owner")
    rows = []
    for n in names:
        full = "%s/%s" % (owner, n) if owner and "/" not in n else n
        row = {"repo": full, "name": n.split("/")[-1], "commits": None,
               "first_commit": None, "last_commit": None, "reachable": False}
        try:
            raw = subprocess.run(["gh", "api", "repos/%s/commits?per_page=100" % full],
                                 capture_output=True, text=True, timeout=30)
            if raw.returncode == 0:
                cs = json.loads(raw.stdout)
                if isinstance(cs, list) and cs:
                    ds = sorted(c["commit"]["author"]["date"] for c in cs)
                    row.update(commits=len(cs), first_commit=ds[0],
                               last_commit=ds[-1], reachable=True)
        except Exception:
            pass
        rows.append(row)
    return {
        "rows": rows,
        "fetched": dt.datetime.now().astimezone().isoformat(),
        "why": (
            "These repositories are part of the same project and were worked "
            "on from another machine. The usage store is a local SQLite file "
            "with no API to query, so their requests, tokens and cost are not "
            "in any figure on this page. Commit counts come from GitHub, which "
            "is why they are visible when the spend is not. To fold them in, "
            "run `usage-calc export` on that machine and drop the files into "
            "the contributions directory."
        ),
        "caveat": (
            "Every total on this page is therefore a FLOOR for the project as "
            "a whole, and an exact figure only for the repository it was "
            "generated in."
        ),
    }


def outputs(root):
    """The other half of the ledger: what the project has to show for it.

    VOLUME IS NOT VALUE. This only counts; the dashboard says so in words.
    """
    commits = []
    for line in [l for l in git(root, "log", "--pretty=format:%h|%aI|%s").splitlines()
                 if l.strip()]:
        h, iso, subj = line.split("|", 2)
        commits.append({"sha": h, "at": iso, "subject": subj})
    ins = dele = 0
    for line in git(root, "log", "--pretty=tformat:", "--numstat").splitlines():
        p = line.split("\t")
        if len(p) >= 3 and p[0].isdigit() and p[1].isdigit():
            ins += int(p[0])
            dele += int(p[1])
    files, by_ext, total_bytes, words = git(root, "ls-files").splitlines(), {}, 0, 0
    for f in files:
        ext = os.path.splitext(f)[1].lower() or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
        p = os.path.join(root, f)
        if os.path.exists(p):
            total_bytes += os.path.getsize(p)
            if ext == ".md":
                try:
                    with open(p, encoding="utf-8") as fh:
                        words += len(fh.read().split())
                except OSError:
                    pass
    return {
        "commits": list(reversed(commits)),
        "commit_count": len(commits),
        "insertions": ins,
        "deletions": dele,
        "tracked_files": len(files),
        "files_by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
        "bytes": total_bytes,
        "markdown_words": words,
    }


def offered_models(used):
    """How many models the client offered, against how many were used.

    models.json is written beside the CLI debug log. Absent it, say so rather
    than guessing a roster - "offered: null" is a fact and an invented number
    is not.
    """
    base = os.path.join(os.environ.get("APPDATA", ""), "Code", "User",
                        "workspaceStorage")
    best = None
    for dirpath, _dirs, names in (os.walk(base) if os.path.isdir(base) else []):
        if "models.json" in names:
            p = os.path.join(dirpath, "models.json")
            if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                best = p
    if not best:
        return {"offered": None, "used": sorted(used), "source": None}
    try:
        with open(best, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"offered": None, "used": sorted(used), "source": None}
    roster = []
    for m in data:
        tp = (m.get("billing") or {}).get("token_prices", {}).get("default") or {}
        roster.append({
            "id": m.get("id"),
            "vendor": m.get("vendor"),
            "input_price": tp.get("input_price"),
            "output_price": tp.get("output_price"),
            "picker": m.get("model_picker_enabled", False),
            "used": m.get("id") in used,
        })
    return {
        "offered": len(roster),
        "selectable": sum(1 for r in roster if r["picker"]),
        "used": sorted(used),
        "roster": sorted(roster, key=lambda r: -(r["input_price"] or 0)),
        "source": "models.json written by the client",
    }
