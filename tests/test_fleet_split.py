"""The fleet-wide cost split, and the things it must refuse.

A dashboard panel that reports one repository's money under a heading covering
five is not a crash. It is a card full of plausible figures with nothing on the
page to suggest anything is wrong, and it shipped. These tests pin the guards
that make that state impossible to reach quietly.

The fixture is SYNTHETIC on purpose. A test that read the real contribution
files would pass or fail depending on which machine it runs on, and those files
cannot be regenerated from this checkout.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from usagecalc.contrib import (_by_model_channel, _fleet_counterfactual,
                               _price_table, _source_channels)
from usagecalc.store import StoreError


def ev(model, chans, nano):
    """One billed request. `chans` maps channel -> (tokens, nano, price)."""
    return {
        "model": model,
        "nano": nano,
        "chans": {k: {"tokens": t, "nano_aiu": n, "price_nano_per_token": p}
                  for k, (t, n, p) in chans.items()},
    }


# One priced model on two channels, so a price table can be learned from it.
# Token counts are scaled so the fixture produces figures a human would
# recognise as money: a rounded dollar assertion on a fixture billing fractions
# of a cent compares 0.00 to 0.00 and passes whatever the code does.
# input 1M tok @ 500 = 500,000,000; cache_read 10M tok @ 100 = 1,000,000,000.
PRIMARY = [
    ev("opus", {"input": (1_000_000, 500_000_000, 500),
                "cache_read": (10_000_000, 1_000_000_000, 100)}, 1_500_000_000),
    ev("opus", {"input": (2_000_000, 1_000_000_000, 500),
                "cache_read": (20_000_000, 2_000_000_000, 100)}, 3_000_000_000),
]


def primary_source(events):
    """A source shaped as fleet() builds one for the machine read live."""
    return {"label": "main", "primary": True, "events": events,
            "channel_totals": None}


def contrib_source(channels, events):
    """A source shaped as fleet() builds one from a contribution file."""
    return {"label": "sibling", "primary": False, "events": events,
            "channel_totals": channels}


class SourceChannels(unittest.TestCase):
    def test_a_contribution_split_matching_its_own_bill_is_accepted(self):
        src = contrib_source(
            [{"type": "input", "tokens": 10, "nano_aiu": 400},
             {"type": "output", "tokens": 5, "nano_aiu": 600}],
            [ev("opus", {"input": (10, 400, 40)}, 1000)])
        rows = _source_channels(src)
        self.assertEqual(sum(v["nano_aiu"] for v in rows.values()), 1000)

    def test_a_split_that_does_not_match_its_own_bill_is_REFUSED(self):
        # The guard that matters. Merging a split that does not add up produces
        # a fleet total wrong by exactly the discrepancy, and every figure
        # downstream of it stays internally consistent.
        src = contrib_source(
            [{"type": "input", "tokens": 10, "nano_aiu": 400},
             {"type": "output", "tokens": 5, "nano_aiu": 599}],
            [ev("opus", {"input": (10, 400, 40)}, 1000)])
        with self.assertRaises(StoreError):
            _source_channels(src)

    def test_an_unknown_billing_channel_is_REFUSED(self):
        src = contrib_source(
            [{"type": "telepathy", "tokens": 10, "nano_aiu": 1000}],
            [ev("opus", {"input": (10, 1000, 100)}, 1000)])
        with self.assertRaises(StoreError):
            _source_channels(src)

    def test_the_primary_is_summed_from_its_own_rows(self):
        rows = _source_channels(primary_source(PRIMARY))
        self.assertEqual(rows["input"]["nano_aiu"], 1_500_000_000)
        self.assertEqual(rows["cache_read"]["nano_aiu"], 3_000_000_000)
        self.assertEqual(sum(v["nano_aiu"] for v in rows.values()),
                         4_500_000_000)


class PriceTable(unittest.TestCase):
    def test_a_price_is_learned_per_model_AND_channel(self):
        price = _price_table(PRIMARY)
        self.assertEqual(price[("opus", "input")], 500)
        self.assertEqual(price[("opus", "cache_read")], 100)

    def test_a_cheap_model_does_not_set_an_expensive_model_s_rate(self):
        # A source's own blended nano/token average is dragged by whichever
        # model it happened to run, which is why the table is keyed on the
        # model rather than learned from a source total.
        evs = PRIMARY + [ev("haiku", {"input": (1_000_000, 10_000_000, 10)}, 10_000_000)]
        price = _price_table(evs)
        self.assertEqual(price[("opus", "input")], 500)
        self.assertEqual(price[("haiku", "input")], 10)


class Counterfactual(unittest.TestCase):
    def test_it_reprices_cached_tokens_at_the_full_input_rate(self):
        cf = _fleet_counterfactual([primary_source(PRIMARY)],
                                   _price_table(PRIMARY))
        # 3,000 cached tokens repriced from 100 to 500 is a real uplift.
        self.assertGreater(cf["uncached_usd"], cf["actual_usd"])
        self.assertGreater(cf["ratio"], 1.0)
        self.assertEqual(cf["models_without_an_input_price_sample"], [])

    def test_a_price_table_that_does_not_reproduce_a_source_is_REFUSED(self):
        bad = dict(_price_table(PRIMARY))
        bad[("opus", "input")] = 1  # not the rate this source was billed at
        with self.assertRaises(StoreError):
            _fleet_counterfactual([primary_source(PRIMARY)], bad)

    def test_an_unpriced_model_is_charged_at_ACTUAL_and_NAMED(self):
        # Charging an unpriced model at its actual cost adds no uplift, which
        # makes the repriced column a FLOOR rather than an estimate. A floor is
        # only honest if it says so, hence the name being carried out.
        haiku = ev("haiku", {"input": (1_000_000, 10_000_000, 10)}, 10_000_000)
        del haiku["chans"]["input"]["price_nano_per_token"]
        evs = PRIMARY + [haiku]
        cf = _fleet_counterfactual([primary_source(evs)], _price_table(evs))
        self.assertIn("haiku", cf["models_without_an_input_price_sample"])
        self.assertGreaterEqual(cf["uncached_usd"], cf["actual_usd"])


class ByModelChannel(unittest.TestCase):
    def test_tokens_and_cost_are_grouped_without_loss(self):
        toks, nano = _by_model_channel(PRIMARY)
        self.assertEqual(toks[("opus", "input")], 3_000_000)
        self.assertEqual(toks[("opus", "cache_read")], 30_000_000)
        self.assertEqual(nano["opus"], 4_500_000_000)
        self.assertEqual(sum(nano.values()), sum(e["nano"] for e in PRIMARY))


if __name__ == "__main__":
    unittest.main()
