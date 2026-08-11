# usage-calc

**What a Copilot CLI project actually cost — measured, not estimated.**

- [What this is](#what-this-is)
- [Install](#install)
- [Use it in five minutes](#use-it-in-five-minutes)
- [Working across more than one machine](#working-across-more-than-one-machine)
- [What the numbers mean](#what-the-numbers-mean)
- [Two aggregations that look obvious and are wrong](#two-aggregations-that-look-obvious-and-are-wrong)
- [What is retained, and what that does not establish](#what-is-retained-and-what-that-does-not-establish)
- [Privacy: what an export contains](#privacy-what-an-export-contains)
- [Command reference](#command-reference)
- [Where this is likely to be wrong](#where-this-is-likely-to-be-wrong)
- [Licence](#licence)

---

## What this is

The GitHub Copilot CLI writes a local SQLite ledger as it works: one row per
request, carrying the model, the token counts, the duration, and **the price it
was billed at**. Nothing has to be switched on and nothing uploads it.

`usage-calc` reads that ledger and produces a **single self-contained HTML page**
with no runtime dependencies — no CDN, no build step, no server — that says what
a project cost in money, in tokens, in model time and in human time, together
with an explicit account of what it could not measure.

It was extracted from a research project that used it on itself. That project's
figures are the worked example throughout this document.

**Why bother.** Almost every public claim about what AI-assisted work costs rests
on an invented denominator — a remembered number of hours, a guessed number of
requests, a rate nobody published. This reads a ledger that was written as the
work happened, and refuses to print a total it cannot reconcile.

**What it is not.** It is not a billing tool. The dollar figure is a *list-price
equivalent*: it prices tokens at the rates the client publishes and converts at
GitHub's documented 1 credit = $0.01. A Copilot subscription bills *premium
requests*, which is a different quantity. The page says so on its face.

---

## Install

Python 3.8+, standard library only. Node is needed **only** for the optional
browser verifier.

```bash
pip install git+https://github.com/Ethical-Tech-CoLab/usage-calc.git
```

Or clone and work from the checkout, which is what you want if you intend to
change the template:

```bash
git clone https://github.com/Ethical-Tech-CoLab/usage-calc.git
cd usage-calc
pip install -e .
```

No install at all is also fine — `python -m usagecalc.cli …` works from a
checkout.

---

## Use it in five minutes

From inside the project you want to measure:

```bash
usage-calc init          # writes usage-calc.json
usage-calc build         # writes usage/usage-data.json and usage/usage-dashboard.html
```

Open `usage/usage-dashboard.html`. That is the whole deliverable — one file you
can commit, email or publish to Pages.

`usage-calc.json` is small and every key is optional:

```json
{
  "title": "my-project",
  "out_html": "usage/usage-dashboard.html",
  "out_json": "usage/usage-data.json",
  "contrib_dir": "usage/contrib",
  "siblings": [],
  "owner": null,
  "turn_labels": true
}
```

**Re-running `build` splices new numbers into an existing page rather than
replacing it**, so if you restyle your copy of the template you will not lose
the styling. Pass `--fresh` when you *do* want the packaged template back.

---

## Working across more than one machine

**This is the part people get wrong, and it is worth reading even if you think
you only use one machine.**

The store is **per machine**. It is a local SQLite file and there is no API that
can ask another machine what it spent. A dashboard built on one laptop is
therefore a **floor**, not a total, for anyone who also works from a desktop.

On a real two-machine project the gap was **40 per cent**: $599.62 became
$1,484.30, and 4,711 requests became 8,086.

Fix it in two steps. On the *other* machine:

```bash
# tools/export_session.py is standalone - no install, no checkout needed
python export_session.py --list
python export_session.py --all --out .
```

Copy the resulting JSON files into your project's `usage/contrib/`, then rebuild.
`usage-calc build` merges them and every headline becomes project-wide with the
local share named beneath it.

**A store is scoped to a machine, not to a repository.** One store holds every
project you have worked on from that box, so a single `--all` sweeps all of them
— you do not need to run anything per repository. What you *do* need is to
remember every machine. Nothing in the data can tell you about a machine you
forgot.

### Money is additive and a person is not

This is the rule the merge is built on:

| Quantity | Across machines | Why |
|---|---|---|
| Requests, tokens, cost | **Sum** | Two machines spending at once really do spend twice |
| Model *work* seconds | **Sum** | Both models really were generating |
| Model *wall* time | **Union** | Only one minute of clock passed |
| Engaged time | **Union of sittings over the pooled stream** | One person, who cannot be at two keyboards at once |
| Person time | **Recomputed** from the merged clock | Never the sum of per-machine residuals |

Summing person-hours per machine would inflate the weakest column on the page
and do it **invisibly** — every figure would still reconcile, because both sides
of the identity would have grown together.

### The cut-off belongs to the person, not the keyboard

An earlier version cut each machine's stream into sittings separately and then
unioned the results. That applies the idle cut-off **per keyboard**: a
three-minute gap counted as engaged when both requests landed on one machine and
as a pause when the second landed on the other — the same person turning to the
other screen.

Cutting the **pooled** stream instead is worth:

| Idle cut-off | Per machine | Pooled | Bridged |
|---|---|---|---|
| 2 min | 30.88 h | 30.91 h | +2.2 min |
| 5 min | 32.07 h | 32.23 h | **+9.5 min** |
| 10 min | 35.54 h | 35.71 h | +10.1 min |
| 30 min | 39.62 h | 39.98 h | **+21.4 min** |

**The gap widens with the cut-off, which is the signature of a mechanism rather
than of rounding** — a longer cut-off bridges more gaps, so it bridges more
cross-machine ones. Both readings are published, and the verifier asserts
`pooled >= per-machine` so the fix cannot silently revert.

**What found the original bug: two cards on the same page disagreeing by nine
minutes about the same quantity.** No assertion caught it, because both cards
were internally consistent.

---

## What the numbers mean

The page publishes two kinds of number and always says which is which.

> **Model time is MEASURED.** It is a union of intervals the client recorded.
>
> **Person time is INFERRED.** It is what is left of a sitting once the model was
> not generating — and that is not the same thing as a person being present.

Within a sitting either a model was working or it was not, so

```
engaged = model + person
```

holds by construction and person time can never come out negative. That identity
is what makes the split defensible. It does **not** prove anybody was there. The
residual errs in **both** directions: it over-counts when somebody walked away
mid-sitting, and it under-counts the reading done *after* a sitting's last
request — which, for research work, is exactly when the reading happens.

**The sensitivity is the argument.** Model time does not move when you change the
idle cut-off, because it is measured. Person time moves by a factor of two or
three across the offered range, because it is a residual of an arbitrary choice.
The page offers all four cut-offs so a reader can see which is which without
taking anyone's word for it.

### Days are cut on local midnight

Not UTC. On the source project **19 per cent of requests landed between 00:00 and
04:00 UTC** — the previous evening locally. Cutting on UTC days filed a fifth of
the work under the wrong date and moved one day by a factor of five: 404 requests
by UTC day against 80 by local day.

Local days come from the platform's own zone database via `astimezone()`, not
from `zoneinfo`, because `zoneinfo` needs the `tzdata` package that a clean
Windows Python does not have — and a build script that dies on a bare interpreter
is a landmine.

---

## Two aggregations that look obvious and are wrong

**1. `SUM(duration_ms)` overstates model time.** Sub-agent requests run *beside*
the main agent, not after it, so their durations overlap. On the source session
the naive sum read **16.79 h against a true union of 14.99 h — 12 per cent high**,
and 15.2 per cent high over a sub-agent-heavy week. `usage-calc` unions the
intervals.

**2. The token columns disagree with `token_details_json`.** The flat columns are
missing or wrong on compaction rows. Cost is therefore read from the details,
which carry their own per-channel rates, and **the package refuses to run** if
they do not reconcile to the row's own recorded total. A total it cannot
reconcile is worse than no total, because it looks exactly like one that
reconciles.

The generator also refuses to write output if daily model time differs from the
measured union by more than a second at any cut-off. That assertion is how the
overlapping-sittings bug was found.

---

## What is retained, and what that does not establish

Observed on a real store: rows go back to the first request of the oldest
session, nothing was pruned over ten days of continuous use, and sessions older
than the usage table survive with turns but zero usage rows — which is the table
arriving late, not deletion.

**What this cannot establish: whether a 30- or 90-day retention policy exists.**
No usage older than ten days existed on that machine, so such a policy would be
invisible. If your accounting depends on data older than that, export
periodically rather than assuming it will still be there.

---

## Privacy: what an export contains

A contribution file contains **counts, timestamps, durations, model names and
prices**. It contains no prompt text, no responses, no file contents, no
summaries and no turn labels. Every file carries
`"contains_prompt_text": false`, and it is true.

The only function in the package that touches prompt text is `turn_labels()`,
which reads the first line of each user message for the "what was asked" list on
your own dashboard. It is never exported. Set `"turn_labels": false` in
`usage-calc.json` to switch it off entirely.

---

## Command reference

| Command | What it does |
|---|---|
| `usage-calc init` | Write a `usage-calc.json` into the current project |
| `usage-calc sessions` | List what the local store holds, with request counts |
| `usage-calc build` | Generate the JSON payload and the dashboard |
| `usage-calc export` | Export this machine's usage for another machine to merge |
| `usage-calc query` | What has this machine been doing lately, across all projects |
| `usage-calc report` | Print merged contributions without building a page |
| `usage-calc verify` | Drive the dashboard in a browser and check it against its own data |

`usage-calc query` answers the question the dashboard cannot:

```bash
usage-calc query --days 7            # last week, by session
usage-calc query --by repo --days 30
usage-calc query --by day --json
```

Everything is read-only. The store is snapshotted before reading, so all of it is
safe to run while the CLI is working.

---

## Where this is likely to be wrong

Written in the same spirit as the dashboard's own wrong-list.

1. **The dollar figure is a list-price equivalent, not a bill.** It is the right
   number for comparing runs and the wrong number for reconciling an invoice.
2. **`nano_aiu` is assumed to be denominated in the documented AI credit.** The
   arithmetic matches published per-model rates to the cent, which is strong
   evidence and not proof.
3. **Person time is a residual and is stated as one.** It cannot distinguish
   thinking from lunch.
4. **The energy panel is an estimate stapled to a fact.** No joules are recorded
   anywhere. The two ends of its own published bracket differ by a factor of 24,
   which is why it is never collapsed to a single number.
5. **A machine you forgot is invisible.** The merge can only include what was
   exported. Nothing in the data can tell you that a third machine exists.
6. **Retention beyond ten days is unestablished** — see above.
7. **Concurrent time is measured; simultaneous attention is not.** Two models
   generating at once is a fact in the data. One person meaningfully attending to
   both is an assumption, and probably a false one.
8. **Volume is not value.** Nothing here measures whether any of the output was
   worth having.

---

## Licence

MIT. See [LICENSE](LICENSE).

Built by [Ethical Tech CoLab](https://github.com/Ethical-Tech-CoLab).
