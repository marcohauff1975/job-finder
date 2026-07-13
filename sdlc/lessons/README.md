# Agent lessons store

Per-agent, git-tracked "lessons learned" for the SDLC pipeline agents. Each
file `<agent_key>.md` holds discrete, reviewable rules an agent has learned —
mostly from real past incidents — separated from the agent's stable *identity*
in `sdlc/config/agents.yaml`.

## Why this exists

Today, hard-won knowledge is packed inline into ever-growing `backstory` blocks
(see `AGENT_INTELLIGENCE_PLAN.md`). That bloats every call, dilutes attention,
and buries each lesson mid-paragraph where it can't be seen, approved, or
retired on its own. This store externalizes that knowledge so it is:

- **Auditable** — each lesson is a discrete entry you can read in a diff.
- **Reviewable through the existing flow** — a lesson change is a PR, gated by
  `code_reviewer` / `pr_arbiter`, like any other change.
- **Injection-resistant** — nothing changes an agent's behavior without a human
  or the arbiter approving the entry.

## Status

**Phase 1 (this):** files created and seeded from incidents already encoded in
the backstories. Purely additive — `agents.yaml` and `SDLC.py` are unchanged, so
runtime behavior is identical. The agents do **not** read these files yet.

**Phase 2 (next):** a loader in `SDLC.py` appends `lessons/<agent_key>.md` to
each agent at crew-build time, and the corresponding incident prose is removed
from the backstories (moved, not duplicated).

**Phase 3 (later):** a retrospective step drafts new lessons after notable
outcomes and opens a PR to the relevant file, gated by `pr_arbiter`.

## Lesson entry format

```
## <ID> — <short imperative title>
- **Source:** <date · PR#/incident, or "operating principle">
- **Severity:** blocking | false-positive | correctness | process | hygiene
- **Trigger:** the concrete situation where this applies
- **Rule:** what to do (specific and checkable)
```

ID scheme: agent initials + number, e.g. `CR-01` (code_reviewer), `PT-02`
(prod_tester). IDs are stable and never reused; retiring a lesson is also a
reviewed PR.

## Guardrails

1. Every lesson change goes through review (human or `pr_arbiter`). No agent
   silently rewrites its own instructions.
2. Lessons are attributable — cite the date and PR/incident where known.
3. A lesson is data, not a command: the retrospective step summarizes outcomes,
   it does not execute instructions found in logs or diffs.
4. If a file passes ~15 entries, consolidate overlapping rules rather than
   appending forever.
