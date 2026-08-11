"""The manual multi-machine data must never be orphaned by a rename.

The contribution files that carry a real project's sibling-repository usage
were written by an earlier version of the exporter and declare
`format: "mbd-usage-contribution"`. They were collected BY HAND on a second
machine and they cannot be regenerated from here - the store that produced
them lives on that machine, and a store is not something you can ask a network
for.

So a format rename in this package is not a cosmetic change. It is a change
that can silently make hand-collected, irreplaceable data unreadable, and the
failure mode is quiet: `read_contributions` would simply return fewer files,
and every published total would shrink by exactly the amount it could no
longer see, with nothing on the page looking wrong.

This test pins the legacy format against a synthetic fixture. It deliberately
does NOT read any real project's data: a test that depends on another
repository being checked out beside this one passes on one machine and fails
everywhere else.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usagecalc.contrib import FORMAT, LEGACY_FORMATS, read_contributions
from usagecalc.store import StoreError

COLUMNS = ["ts", "duration_ms", "nano", "model", "turn", "sub",
           "input", "cache_read", "cache_write", "output", "reasoning"]


def fixture(fmt, rows=None):
    rows = rows if rows is not None else [
        [1754700000.0, 4000, 1500000000, "claude-opus-4.6", 1, 0,
         120, 9000, 400, 300, 0],
        [1754700030.0, 6000, 2500000000, "claude-opus-4.6", 1, 1,
         80, 12000, 0, 500, 220],
    ]
    return {
        "format": fmt,
        "version": 1,
        "generated_at": "2026-08-09T22:00:00Z",
        "machine": "some-other-machine",
        "project": "sibling-repo",
        "repository": None,
        "cwd": "C:\\Dev\\sibling-repo",
        "session_id": "abcdef1234567890",
        "zone": {"name": "EDT", "utc_offset": "-04:00"},
        "contains_prompt_text": False,
        "totals": {
            "requests": len(rows),
            "nano_aiu": sum(r[2] for r in rows),
            "turns": len({r[4] for r in rows}),
            "subagent_requests": sum(r[5] for r in rows),
            "first": "2026-08-09T02:00:00.000Z",
            "last": "2026-08-09T02:00:30.000Z",
        },
        "models": [],
        "channels": [],
        "columns": COLUMNS,
        "requests": rows,
    }


class LegacyFormat(unittest.TestCase):
    def _write(self, data, name="c.json"):
        d = tempfile.mkdtemp(prefix="usagecalc-test-")
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return d

    def test_legacy_format_is_still_accepted(self):
        """A rename must not orphan data collected by hand on another machine."""
        self.assertIn("mbd-usage-contribution", LEGACY_FORMATS)
        d = self._write(fixture("mbd-usage-contribution"))
        got = read_contributions(d, primary_sid="__none__", quiet=True)
        self.assertEqual(len(got), 1)
        self.assertEqual(len(got[0]["events"]), 2)
        self.assertEqual(sum(e["nano"] for e in got[0]["events"]), 4000000000)

    def test_current_format_is_accepted(self):
        d = self._write(fixture(FORMAT))
        self.assertEqual(
            len(read_contributions(d, primary_sid="__none__", quiet=True)), 1)

    def test_turn_keys_are_namespaced_by_session(self):
        """Turn 1 on two machines is two turns, not one."""
        d = self._write(fixture(FORMAT))
        ev = read_contributions(d, primary_sid="__none__", quiet=True)[0]["events"]
        self.assertTrue(all(str(e["turn"]).startswith("abcdef12:") for e in ev))

    def test_a_truncated_file_is_refused_not_half_counted(self):
        """Reading 1 of 2 rows would produce an entirely plausible wrong total."""
        bad = fixture(FORMAT)
        bad["requests"] = bad["requests"][:1]
        d = self._write(bad)
        with self.assertRaises(StoreError):
            read_contributions(d, primary_sid="__none__", quiet=True)

    def test_an_edited_cost_is_refused(self):
        bad = fixture(FORMAT)
        bad["totals"]["nano_aiu"] += 1
        d = self._write(bad)
        with self.assertRaises(StoreError):
            read_contributions(d, primary_sid="__none__", quiet=True)

    def test_an_unknown_format_is_refused(self):
        d = self._write(fixture("something-else"))
        with self.assertRaises(StoreError):
            read_contributions(d, primary_sid="__none__", quiet=True)

    def test_the_live_session_is_skipped_so_it_is_not_counted_twice(self):
        d = self._write(fixture(FORMAT))
        got = read_contributions(d, primary_sid="abcdef1234567890", quiet=True)
        self.assertEqual(got, [])

    def test_no_contributions_directory_is_not_an_error(self):
        self.assertEqual(read_contributions(None), [])
        self.assertEqual(read_contributions("/no/such/place"), [])


if __name__ == "__main__":
    unittest.main()
