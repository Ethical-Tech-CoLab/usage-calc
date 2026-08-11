"""What the repository selector must never claim.

The selector exists because the page previously carried several half-scopes -
a day chart offering "this repository / all repositories", and other panels
that were silently one or the other with only prose to tell them apart. The
failure mode that matters is not a crash. It is a panel showing the primary
repository's numbers under a sibling's name, which renders as a perfectly
ordinary card full of plausible figures.

So every test here is about a CAPABILITY FLAG being honest, and about the one
number that may legitimately be summed across repositories.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usagecalc.build import _scopes


def fleet(*rows):
    return {
        "sources": list(rows),
        "totals": {"requests": sum(r["requests"] for r in rows),
                   "usd": round(sum(r["usd"] for r in rows), 2)},
        "days": {"rows": [{"date": "2026-08-11"}]},
    }


def src(name, primary=False, requests=100, usd=10.0, days=True, nano=1):
    r = {"project": name, "primary": primary, "requests": requests,
         "usd": usd, "nano_aiu": nano, "machine": "m1"}
    if days:
        r["day_rows"] = [{"date": "2026-08-11", "requests": requests}]
    return r


def sibs(*rows):
    return {"rows": list(rows)}


def sib(name, commits=20):
    return {"name": name, "commits": commits,
            "first_commit": "2026-08-01", "last_commit": "2026-08-09"}


OUTS = {"commit_count": 41, "lines": 1000, "words": 2000}


class ScopeTest(unittest.TestCase):

    def build(self, f=None, s=None, o=OUTS):
        return _scopes("main-repo", None, f, s, o)

    def by_key(self, sc):
        return {e["key"]: e for e in sc["entries"]}

    # -- the plan is primary-only, and that is not a bug to be smoothed over --
    def test_plan_is_primary_only(self):
        sc = self.build(fleet(src("main-repo", primary=True), src("sib-a")),
                        sibs(sib("sib-a")))
        e = self.by_key(sc)
        self.assertTrue(e["main-repo"]["plan"])
        self.assertFalse(e["sib-a"]["plan"])
        self.assertFalse(e["all"]["plan"])

    # -- lines and words need a checkout; commits do not --------------------
    def test_output_capability_by_kind(self):
        sc = self.build(fleet(src("main-repo", primary=True), src("sib-a")),
                        sibs(sib("sib-a")))
        e = self.by_key(sc)
        self.assertEqual(e["main-repo"]["output"], "full")
        self.assertEqual(e["sib-a"]["output"], "commits")
        # "All" may report commits because commits come from the API for every
        # repository. It may NOT report a line count, because that would be one
        # repository's figure under a label covering five.
        self.assertEqual(e["all"]["output"], "commits")

    def test_sibling_with_no_github_row_offers_no_output(self):
        # A repository that exported usage but has no commit row cannot show a
        # commit count. False, not zero: zero is a measurement.
        sc = self.build(fleet(src("main-repo", primary=True), src("quiet")),
                        sibs())
        e = self.by_key(sc)
        self.assertIs(e["quiet"]["output"], False)
        self.assertIsNone(e["quiet"]["commits"])

    # -- the summed commit count, and its honesty flag ----------------------
    def test_all_sums_commits(self):
        sc = self.build(fleet(src("main-repo", primary=True), src("sib-a")),
                        sibs(sib("sib-a", commits=20)))
        e = self.by_key(sc)
        self.assertEqual(e["all"]["commits"], 41 + 20)
        self.assertFalse(e["all"]["commits_partial"])

    def test_commits_partial_when_one_repository_is_unknown(self):
        sc = self.build(
            fleet(src("main-repo", primary=True), src("sib-a"), src("quiet")),
            sibs(sib("sib-a")))
        e = self.by_key(sc)
        self.assertTrue(e["all"]["commits_partial"],
                        "a sum missing a repository must say so")
        self.assertEqual(e["all"]["commits"], 41 + 20)

    # -- a repository nobody exported still belongs in the list -------------
    def test_named_sibling_without_usage_is_listed(self):
        sc = self.build(fleet(src("main-repo", primary=True)),
                        sibs(sib("never-exported")))
        e = self.by_key(sc)
        self.assertIn("never-exported", e)
        self.assertFalse(e["never-exported"]["usage"])
        self.assertFalse(e["never-exported"]["days"])
        self.assertEqual(e["never-exported"]["output"], "commits")

    def test_days_flag_follows_the_rows_that_exist(self):
        sc = self.build(fleet(src("main-repo", primary=True),
                              src("no-days", days=False)),
                        sibs())
        e = self.by_key(sc)
        self.assertTrue(e["main-repo"]["days"])
        self.assertFalse(e["no-days"]["days"])

    # -- shape ---------------------------------------------------------------
    def test_all_is_first_and_main_is_named(self):
        sc = self.build(fleet(src("main-repo", primary=True), src("sib-a")),
                        sibs(sib("sib-a")))
        self.assertEqual(sc["entries"][0]["key"], "all")
        self.assertEqual(sc["default"], "all")
        self.assertEqual(sc["main"], "main-repo")
        self.assertEqual(sc["entries"][1]["kind"], "main")
        self.assertIn("(main)", sc["entries"][1]["label"])

    def test_keys_are_unique(self):
        sc = self.build(fleet(src("main-repo", primary=True), src("sib-a")),
                        sibs(sib("sib-a"), sib("sib-b")))
        keys = [e["key"] for e in sc["entries"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_no_fleet_means_no_selector(self):
        # One repository and no contributions: an empty dropdown is worse than
        # no dropdown.
        self.assertIsNone(self.build(None, None))

    # -- the note is load-bearing prose, not decoration ---------------------
    def test_note_states_that_time_does_not_sum(self):
        sc = self.build(fleet(src("main-repo", primary=True), src("sib-a")),
                        sibs(sib("sib-a")))
        self.assertIn("TIME DOES NOT", sc["note"])


if __name__ == "__main__":
    unittest.main()
