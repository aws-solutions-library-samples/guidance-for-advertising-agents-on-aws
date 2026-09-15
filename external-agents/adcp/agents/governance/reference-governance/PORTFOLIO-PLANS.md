# Portfolio plans: what they are, and why this agent doesn't support them yet

Written for a developer picking this up cold. No prior context needed.

## What a portfolio plan is

Normally, one campaign plan governs one campaign. It says things like "this campaign has a $500,000
budget, runs in February, and may only buy display".

AdCP also allows a plan to govern **other plans**. That's a portfolio plan. Instead of describing a
campaign of its own, it names a group of campaigns and sets limits that apply across the whole group.

A worked example. Say a holding company runs three brands:

```
Portfolio plan: "acme-group-q1"
  member plans:  pantene-q1  ($400k)
                 gillette-q1 ($400k)
                 oral-b-q1   ($400k)
  total budget cap: $1,000,000
```

Each brand plan is allowed $400k. Added up, that's $1.2m. But the portfolio caps the group at $1m.

So a buy can be perfectly fine against its own plan and still have to be refused because the group has
run out. AdCP is explicit about this: a denial from *either* level blocks the action. Two ceilings, both
of which have to be satisfied.

The spec's own wording for why this exists: portfolio plans set "cross-brand constraints that no
individual brand plan can override".

## It's four things, not just a budget

The portfolio block in the AdCP schema carries four fields:

| Field | Required | What it does |
|---|---|---|
| `member_plan_ids` | yes | which plans this portfolio governs |
| `total_budget_cap` | no | maximum spend across all of them combined |
| `shared_policy_ids` | no | policies enforced on every member, from the policy registry |
| `shared_exclusions` | no | bespoke exclusions applied to every member |

This is worth noticing, because it was previously tracked in our own notes as just "the portfolio budget
cap" (item M10). The budget cap is the headline, but the two policy fields are part of the same feature
and carry the same "no member can override this" force.

## What this agent does with a portfolio plan today

Nothing. And that's the part worth understanding, because "nothing" here does not mean "rejects it".

There is no mention of `portfolio`, `member_plan_ids`, `total_budget_cap`, `shared_policy_ids` or
`shared_exclusions` anywhere in the governance agent's code. But `sync_plans` validates the incoming plan
against the AdCP schema — which *does* include the portfolio block — and then saves the whole plan object
to DynamoDB (`main.py`, around line 801).

So the sequence today is:

1. A buyer sends a portfolio plan with a $1m group cap.
2. The agent accepts it and returns success.
3. The cap is written to the database.
4. Every later spend check ignores it completely.

The buyer has been told its group ceiling is registered. It isn't being enforced. Nothing errors and
nothing logs a warning.

**How exposed are we, in practice?** Limited, for now:

- Our own reference buyer can't create a portfolio plan. Its `adcp_sync_plans` tool has no portfolio
  parameter, so there is no path through our own stack that produces one.
- The agent doesn't *claim* portfolio support in `get_adcp_capabilities`. So this is a gap in what we
  offer, not a false advertisement — an important distinction, because a capability we declared and
  didn't honour would be something a buyer could reasonably rely on.

The real exposure is a third-party buyer, which can send whatever the schema allows and would get the
silent behaviour above.

## Why it wasn't built

It was deliberately postponed, with sign-off, when the last governance feature shipped. That feature
closed eleven AdCP "MUST" requirements; this was the one held back, on the grounds that it's roughly as
large as the biggest piece already in that batch.

Looking at it again now, that judgement holds. Here's the shape of the work, in plain terms.

**1. A second running total, which can't reuse the first.**
The agent already keeps a running total of committed spend, bucketed by day over a 30-day window. That
total is keyed per *relationship* — one buyer, one seller, one account. A portfolio cap asks a different
question: total across the member plans, regardless of which seller the money went to. Different
question, different key, so it's a parallel mechanism rather than an extension of the existing one.

Consequences: a new kind of record in the table, a **second database read on the critical path of every
single governance check** (there's currently one, and it's the reason the agent has timeouts and a
circuit breaker at all), and every spend commit now writing two totals instead of one. If one write
succeeds and the other fails, the two ceilings disagree — so that needs a deliberate answer, not a hope.

**2. Finding the portfolio from the member.**
The pointer only runs one way. A portfolio lists its members; a member has no idea it belongs to a
portfolio. But checks arrive *for a member plan*, so the agent has to work backwards from member to
portfolio.

The governance table is keyed on partition key + sort key with no secondary index, so working backwards
means either maintaining a small pointer record per member (written whenever a portfolio is synced), or
scanning the table. Scanning on the path of every check isn't viable. This is the part the original
deferral note didn't mention, and it's the fiddliest bit.

It's also made harder by the spec allowing a portfolio to be registered *before* the plans it names
exist. So the pointer records have to tolerate naming plans that aren't there yet, and get picked up when
those plans arrive later.

**3. A trap in how verdicts are decided.**
The agent's rule for turning findings into a verdict is: a serious problem the caller can't fix →
`denied`; a problem the caller *can* fix → `conditions` (i.e. "adjust this and re-check"); otherwise
`approved`.

A portfolio breach belongs firmly in the first bucket. The caller cannot fix it — they can't raise
someone else's group budget. So it has to be recorded as a problem with no suggested correction attached.
Attach one by mistake and the verdict softens from `denied` to `conditions`, which would let the original
unchanged request through. That exact class of mistake has already happened once in this file's history
(a budget condition accidentally softening an unauthorised-market breach), so it's a known sharp edge
rather than a theoretical one.

**4. The smaller trailing pieces.**
Whatever gets supported has to be declared in the agent's capabilities, and a declaration that doesn't
match the behaviour is worse than saying nothing. The audit log needs to say *which* ceiling caused a
denial, otherwise nobody can explain why a buy that was well within its own budget got refused. And for
any of it to be testable end to end, our buyer needs a way to create portfolio plans and the journey UI
needs somewhere to show the group ceiling.

## The cheap option, if the full feature isn't wanted

Make `sync_plans` **reject** a plan that carries a portfolio block, with a plain "portfolio plans are not
supported by this agent" error.

That's a small, contained change. It doesn't implement anything, but it converts today's silent
non-enforcement into an honest refusal — which is the actual risk sitting in the code right now,
regardless of whether the real feature ever gets built.

---

## Separate matter: secondary indexes on our DynamoDB tables

Noted here because it came up while working through the above, but it is **a different question** and
shouldn't be bundled with portfolio support.

Current state, checked live:

| Table | Keys | Secondary indexes |
|---|---|---|
| `adcp-buyer-agent-sessions` | pk / sk | `gsi1` |
| `adcp-reference-governance-state` | PK / SK | none |
| `adcp-reference-seller-state` | pk / sk | none |

So two of the three agent tables have no secondary index. Whether that's a problem depends on whether
anything needs to query by something other than the primary key, and today almost nothing does:

- The **governance agent** performs no table scan on any request path. Its one scan lives in a deployment
  script (the safety check before enabling record expiry), which is off the request path by design.
- The **reference seller** does scan, in two places in `state.py` — listing media buys, and one similar
  listing. That was a deliberate, documented decision: its design notes say a scan is acceptable at this
  agent's fixture-scale data volume, and that's why no index was added.
- The other sellers and the buyer don't scan at all.

So the honest summary is: not currently broken, and one known deliberate tradeoff. The two things that
would change that are the reference seller's data growing beyond fixture scale, and portfolio support
arriving (which needs the member → portfolio lookup described above).

Deciding whether to add indexes now, pre-emptively, is a separate design conversation. This section is
just the factual starting point for it.
