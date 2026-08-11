"""What the plan module must never do.

Two of these tests exist because the obvious implementation was written first
and was wrong in a way that looked right: it reported a completion rate that
reads 100% for everybody, and it treated a never-touched row as a zero-second
todo rather than as missing data.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usagecalc import todos


SCHEMA = """
create table todos (
    id text primary key,
    title text not null,
    description text,
    status text default 'pending',
    created_at text default (datetime('now')),
    updated_at text default (datetime('now'))
);
create table todo_deps (
    todo_id text not null,
    depends_on text not null,
    primary key (todo_id, depends_on)
);
"""

SECRET = "ACQUIRE NORTHWIND BEFORE THE EMBARGO LIFTS"


def make_session(root, sid, rows, deps=()):
    d = os.path.join(root, sid)
    os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(os.path.join(d, "session.db"))
    con.executescript(SCHEMA)
    con.executemany(
        "insert into todos (id, title, description, status, created_at, updated_at) "
        "values (?,?,?,?,?,?)", rows)
    con.executemany("insert into todo_deps (todo_id, depends_on) values (?,?)", deps)
    con.commit()
    con.close()
    return d


class PlanTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Times are UTC, as SQLite's datetime('now') writes them. 12:00 UTC is
        # the same calendar day in every zone this is likely to run in, which
        # keeps the fixture from depending on the tester's timezone.
        self.rows = [
            ("a", SECRET, SECRET, "done", "2026-01-01 12:00:00", "2026-01-01 12:00:00"),
            ("b", SECRET, SECRET, "done", "2026-01-01 12:00:00", "2026-01-01 12:10:00"),
            ("c", SECRET, SECRET, "done", "2026-01-03 12:00:00", "2026-01-03 12:30:00"),
        ]
        make_session(self.tmp, "S1", self.rows, deps=[("b", "a")])

    def test_no_completion_rate_is_reported(self):
        # Everything in the fixture is done. Any key that would render as
        # "100% complete" is exactly what this module refuses to produce.
        s = todos.summary("S1", root=self.tmp)
        flat = repr(sorted(s.keys()))
        for bad in ("completion", "complete_pct", "done_pct", "progress"):
            self.assertNotIn(bad, flat)

    def test_no_todo_text_escapes_in_the_summary(self):
        # The summary is what gets published. Titles and descriptions are prose
        # about unreleased work and must not travel with it.
        s = todos.summary("S1", root=self.tmp)
        self.assertNotIn(SECRET, repr(s))

    def test_never_touched_rows_are_missing_not_zero(self):
        s = todos.summary("S1", root=self.tmp)
        lt = s["lifetime"]
        self.assertEqual(lt["same_second"], 1)      # row "a"
        self.assertEqual(lt["measurable"], 2)       # rows "b" and "c"
        # Median over the measurable subset is 1800 s. Including the untouched
        # row would drag it to 600 s and the number would be a fiction.
        self.assertEqual(lt["median_s"], 1800.0)

    def test_coverage_is_omitted_rather_than_invented(self):
        s = todos.summary("S1", root=self.tmp)
        self.assertNotIn("coverage", s)

    def test_coverage_counts_requests_not_days(self):
        # Day 2 has no todos and carries most of the spend. A day-count would
        # say 2 of 3 = 67%; the honest figure is the money, 30 of 130 = 23%.
        work = {"2026-01-01": 20, "2026-01-02": 100, "2026-01-03": 10}
        c = todos.summary("S1", work_days=work, root=self.tmp)["coverage"]
        self.assertEqual(c["planned_days"], 2)
        self.assertEqual(c["work_days"], 3)
        self.assertEqual(c["unplanned_days"], ["2026-01-02"])
        self.assertEqual(c["requests_planned"], 30)
        self.assertEqual(c["pct"], 23.1)

    def test_dependencies_are_counted(self):
        s = todos.summary("S1", root=self.tmp)
        self.assertEqual(s["deps"]["edges"], 1)
        self.assertEqual(s["deps"]["todos"], 1)

    def test_a_session_with_no_list_returns_none_not_a_crash(self):
        self.assertIsNone(todos.summary("NOPE", root=self.tmp))
        self.assertEqual(todos.read("NOPE", root=self.tmp), ([], {"edges": 0, "todos": 0}))

    def test_read_always_returns_the_same_shape(self):
        # The failure this guards is a bare [] on one path and a tuple on
        # another: it unpacks fine until a session has no list.
        for sid in ("S1", "NOPE"):
            got = todos.read(sid, root=self.tmp)
            self.assertIsInstance(got, tuple)
            self.assertEqual(len(got), 2)

    def test_titles_are_available_locally_but_are_a_separate_call(self):
        # The text is not unreachable - it is just not in the published path.
        t = todos.titles("S1", root=self.tmp)
        self.assertEqual(len(t), 3)
        self.assertIn(SECRET, t[0]["title"])

    def test_discovery_skips_directories_without_a_store(self):
        os.makedirs(os.path.join(self.tmp, "EMPTY"), exist_ok=True)
        found = {r["session"] for r in todos.sessions_with_todos(root=self.tmp)}
        self.assertEqual(found, {"S1"})

    def test_a_session_db_without_a_todos_table_is_survived(self):
        # Older or partially-initialised session stores exist; they must not
        # take down a metrics run.
        d = os.path.join(self.tmp, "BARE")
        os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(os.path.join(d, "session.db"))
        con.execute("create table something_else (x int)")
        con.commit()
        con.close()
        self.assertIsNone(todos.summary("BARE", root=self.tmp))
        self.assertNotIn("BARE", {r["session"]
                                  for r in todos.sessions_with_todos(root=self.tmp)})

    def test_reading_does_not_write_to_the_users_list(self):
        # This runs against a live CLI's own state directory. A metrics run that
        # mutated someone's working todo list would be indefensible.
        path = todos.db_path("S1", self.tmp)
        before = os.path.getmtime(path)
        todos.summary("S1", root=self.tmp)
        todos.titles("S1", root=self.tmp)
        self.assertEqual(os.path.getmtime(path), before)


if __name__ == "__main__":
    unittest.main()
