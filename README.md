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
# usagecalc/tools/export_session.py is standalone - no install, no checkout needed
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

## One repository selector, and what each panel can actually serve

Once a second machine contributes, the page describes more than one repository,
and a card headlined with one scope beside a card headlined with another is
unreadable — a reader has no way to tell whether two numbers disagree because
the work differed or because the panels did.

So the scopes are built once, in `_scopes()`, and **every panel is handed the
same list**: `All repositories`, the primary repository, and each sibling.
Changing any selector changes them all.

The interesting part is that the panels do **not** all have the same data, and
each entry says so rather than guessing:

| capability | who has it | why not everyone |
|---|---|---|
| `usage` | every repository that exported | — |
| `days` | same | — |
| `plan` | **the primary repository only** | todos live in the session state of the machine the session ran on; a contribution file carries none |
| `output` | `full` for the primary, `commits` for a sibling with a GitHub row, `false` otherwise | lines, words and files need a checkout; commits come from the API |

A panel that cannot honour a selection **says so and shows what it has**. It must
never fall back to the primary repository's numbers under another repository's
name — that renders as an ordinary card full of plausible figures and there is
nothing on the page to suggest anything is wrong. `verify_usage.js` asserts the
negative case directly, and the assertion is proved to fail against a
deliberately broken page rather than assumed to work.

Two consequences worth stating on the page, because both are counter-intuitive:

- **Cost and requests add across repositories. Time does not.** Engaged time is
  cut into sittings over whichever stream is selected, so a pause spent in a
  sibling reads as idle in one view and as work in the other. Only the merged
  view cuts the *pooled* stream, which is the reading that matches one person.
- **`All` may report a summed commit count and may not report a line count.**
  Commits are known for every repository; lines are known for one. A number
  covering one repository under a label covering five is the same
  label/number mismatch the glance band was corrected for.

### One date format

`10-August-2026`, everywhere on the page, from one formatter. `Aug 10, 2026` is
locale-bound, `10/8` is ambiguous between two continents, and a bare ISO stamp is
precise and unreadable. The weekday survives in each bar's tooltip, where it
cannot become a second format. The verifier greps the rendered text for the
legacy forms and fails on any of them.

---

## Upgrading the template on a page you have customised

`build` splices only the payload by default, which is what preserves a project's
own masthead, navigation and prose. It also means **a new panel in a newer
template never reaches an existing page**, so `build` compares
`<!--usage-calc-template:N-->` against the packaged version and prints a note
when they differ.

**Do not resolve that note with `--fresh` on a page you have edited.** `--fresh`
renders the packaged template over the target and discards every hand-written
section, the masthead and the navigation. The failure is not loud; you get a
clean, working, generic page.

Take the new template as a three-way merge instead, with the *previous* template
as the base:

```bash
# base: the template version your page was last built from
git -C /path/to/usage-calc show <old-sha>:usagecalc/templates/dashboard.html > base.html
cp usage/usage-dashboard.html ours.html
cp "$(python -c 'import usagecalc,os;print(os.path.join(os.path.dirname(usagecalc.__file__),"templates","dashboard.html"))')" theirs.html

git merge-file -p --diff3 ours.html base.html theirs.html > merged.html
# resolve any conflict markers, then:
cp merged.html usage/usage-dashboard.html
usage-calc build
```

Conflicts land only where you edited the same lines the template changed, which
is exactly the set a human should look at. Afterwards, count your project's own
markers (masthead elements, nav entries, your own section ids) and confirm they
match what they were before the merge — that check is what distinguishes a merge
from a quiet overwrite.

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

**Todo titles are treated the same way.** The plan panel below reads the
session's own todo list, which is prose a person wrote about unreleased work.
`todos.summary()` returns counts, dates and ratios only; `todos.titles()` exists
for a terminal you are sitting at and is never called by the payload builder. A
test asserts the published summary contains no todo text.

---

## Was any of it planned?

The CLI keeps a second SQLite file per session, next to the billing store:

```
~/.copilot/session-state/<session-id>/session.db
```

That is where the working todo list lives. It is keyed by the same session id
the billing store uses, so the two join — and the join is the only reason it is
worth reading. On its own the list says nothing. Against the money it says
where spending happened with no plan behind it:

```bash
usage-calc plan              # this project
usage-calc plan --all        # every session on this machine that kept a list
```

```
  153 todos, 77 dependency edges over 47 of them
  planning covered 7 of 10 working days
  91% of billed requests (4539 of 4968) happened on a day with a plan
  no plan written on: 2026-08-07, 2026-08-09, 2026-08-10
```

### There is no completion rate, on purpose

The obvious number is "153 of 153 done, 100 %". It is worthless. It reads 100 %
because of how a list is *used*, not how the work went: a session closes items
as it goes, so nothing abandoned is left sitting there marked `pending` to pull
the number down. It was closed, or it was never written. **The rate measures
tidying.** A metric that reads 100 % for everybody, forever, distinguishes
nothing, and shipping it would have been worse than shipping nothing.

Coverage is reported instead, and it is weighted by **requests, not days** — a
single unplanned day carrying a third of the spend should not be one-tenth of
the finding.

### Half the timing data is missing and the panel says so

`updated_at` defaults to `created_at`, so a todo inserted and closed without an
intervening status change carries a zero-second lifetime. That means *never
observed in progress*, not *done instantly*. In the reference session it is 119
of 153 rows. Lifetimes are therefore reported over the measurable subset with
its size stated alongside. Averaging the untouched rows in would have halved the
median and the number would have been a fiction.

### Template versions

`build` splices new numbers into an existing page and never touches its markup —
that is what lets a project keep its own styling. It also means **a panel added
to the template reaches nobody**, and a page that cannot render a panel looks
exactly like a page with nothing to show. So the template carries a version, and
a build against an older page says so:

```
NOTE: usage-dashboard.html was built from template v1; the installed template is v2.
```

Take the new template with `git merge-file`, not with `--fresh` — see
[Upgrading the template on a page you have customised](#upgrading-the-template-on-a-page-you-have-customised).

---

## Command reference

| Command | What it does |
|---|---|
| `usage-calc init` | Write a `usage-calc.json` into the current project |
| `usage-calc sessions` | List what the local store holds, with request counts |
| `usage-calc build` | Generate the JSON payload and the dashboard |
| `usage-calc plan` | How much of the billed work happened on a day with a plan |
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
9. **Plan coverage measures whether a plan was WRITTEN, not whether it was
   followed.** A day with forty todos and no relation between them and the work
   scores identically to a day that was genuinely planned. It is a floor on
   deliberateness, not a measure of it.
10. **A todo list is a partial record of intent at best.** Short sessions, and
    work done in one obvious sweep, legitimately have no list. A low coverage
    figure is a question to ask, not a verdict.

---

## Licence

MIT. See [LICENSE](LICENSE).

Built by [Ethical Tech CoLab](https://github.com/Ethical-Tech-CoLab).
