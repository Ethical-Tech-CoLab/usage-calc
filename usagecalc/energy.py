"""Electricity, bracketed rather than estimated.

NO JOULE COUNT REACHES A CLIENT. Nothing in the session store measures energy,
and nothing this package can compute will change that. What follows applies
published per-query figures to a request count, which is AN ESTIMATE STAPLED
TO A FACT - and the dashboard says so in those words rather than presenting
the product as a measurement.

The spread between the lowest and highest published scenario is a factor of
24. Collapsing that to a single number would be false precision dressed as
accounting, so `energy()` returns the whole bracket and the renderer prints
all of it.

Every constant carries the sentence it was read from, so a reader can check it
rather than trust it. A project wanting different figures passes its own
scenarios; a project wanting none passes `energy=False` to the builder.
"""

ENERGY_SOURCE = {
    "title": "Energy Use of AI Inference: Efficiency Pathways and Test-Time Compute",
    "authors": "Oviedo, Kazhamiaka, Choukse, Kim, Luers, Nakagawa, Bianchini, "
               "Lavista Ferres",
    "venue": "Joule, 2025 (Microsoft Research)",
    "url": "https://www.microsoft.com/en-us/research/publication/"
           "energy-use-of-ai-inference-efficiency-pathways-and-test-time-compute",
    "rating": "5/5 VERIFIED for the figures; 2/5 for their application here",
    "locus": "we estimate a median energy per query of 0.34 Wh (IQR: 0.18-0.67) "
             "for frontier-scale models (>200 billion parameters) ... Extending "
             "to test-time scaling scenarios with 15x more tokens per typical "
             "query, the median energy rises 13-fold to 4.32 Wh",
}
ENERGY_SCENARIOS = [
    ("Traditional query, IQR low", 0.18),
    ("Traditional query, median", 0.34),
    ("Traditional query, IQR high", 0.67),
    ("Test-time scaling, median", 4.32),
]
# "The average U.S. household consumes about 10,500 kilowatthours (kWh) of
# electricity per year." - US EIA, Electricity use in homes. 5/5 VERIFIED.
HOUSEHOLD_KWH_PER_YEAR = 10500
HOUSEHOLD_SOURCE = {
    "locus": "The average U.S. household consumes about 10,500 kilowatthours "
             "(kWh) of electricity per year.",
    "url": "https://www.eia.gov/energyexplained/use-of-energy/"
           "electricity-use-in-homes.php",
    "venue": "US Energy Information Administration",
}


def energy(requests, scenarios=None):
    """Bracket the electricity, and refuse to collapse it to one number."""
    scenarios = scenarios or ENERGY_SCENARIOS
    per_day = HOUSEHOLD_KWH_PER_YEAR / 365.0
    rows = []
    for name, wh in scenarios:
        kwh = requests * wh / 1000.0
        rows.append({
            "scenario": name,
            "wh_per_request": wh,
            "kwh": round(kwh, 3),
            "household_hours": round(kwh / per_day * 24, 2),
        })
    return {
        "requests": requests,
        "scenarios": rows,
        "spread": round(rows[-1]["kwh"] / rows[0]["kwh"], 1) if rows[0]["kwh"] else None,
        "measured": False,
        "why_neither_regime_fits": (
            "The published regimes are separated by output length. An agentic "
            "coding workload is not separated that way: its requests are short "
            "in output and extremely long in input, and almost all of that "
            "input is served from a prompt cache. The bracket below is "
            "therefore wider than the truth in one direction and narrower in "
            "another, and no arithmetic available here can close it."
        ),
        "source": ENERGY_SOURCE,
        "household_source": HOUSEHOLD_SOURCE,
    }
