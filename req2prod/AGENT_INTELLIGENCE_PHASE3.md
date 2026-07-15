# Phase 3 — Retrospective loop (continuous learning)

Status: design for review (no code yet)
Builds on: `AGENT_INTELLIGENCE_PLAN.md` (Phases 1–2, live) and the lessons
store in `req2prod/lessons/`.

## Goal

Close the loop: after a **notable** pipeline outcome, capture the lesson and
feed it back into the lessons store so the agents keep improving on their own —
**without ever letting an agent silently rewrite its own instructions.**

Today this happens by hand (an incident → someone edits a lesson file). Phase 3
turns that into: incident → *drafted* lesson → *reviewed* PR → merged.

## Non-negotiable guardrails

1. **Human/arbiter approves every lesson.** The retrospective agent never edits
   `req2prod/lessons/*` on `main`. It opens a **PR**; the lesson only takes
   effect after `pr_arbiter` or Marco approves the merge. This is the defense
   against prompt-drift and against a poisoned outcome becoming permanent
   behavior.
2. **A lesson is data, not a command.** The agent *summarizes* an outcome; it
   never executes instructions found in logs, diffs, or error text it reads.
3. **One lesson per retrospective, attributable.** Each drafted lesson cites its
   source (run id / PR # / date) and targets exactly one agent.
4. **Bounded cost.** Runs only on notable outcomes (not every green run), one
   cheap agent per outcome.

## What counts as a "notable outcome" (the triggers)

Only signal-bearing outcomes — the ones that already represent something going
wrong or a judgment call the pipeline struggled with. All are already
detectable in the current pipeline:

| Trigger | Where it's detected | Candidate lesson target |
|---|---|---|
| Production rollback fired | `rollback_agent` / `RollbackResult` | `prod_tester`, `rollback_agent` |
| Review couldn't converge → arbiter | `pr_arbiter` invoked | `code_reviewer`, `pr_fix_agent` |
| DevOps circuit breaker tripped | new breaker step (PR #52) | `devops_agent` |
| Prod smoke test failed | `prod_tester` / `ProdTestResult` | `prod_tester` |
| Feature build returned no/invalid PR | `build_feature` validation | `software_engineer` |

Explicitly **out of scope for v1** (hard to attribute automatically): "a review
missed a bug that later broke prod." Linking a prod failure back to the specific
review that missed it is a stretch goal, not v1.

## The retrospective agent

- **Role:** read the artifacts of one notable outcome and decide whether there
  is a *generalizable* lesson (not a one-off). If yes, draft exactly one lesson
  in the existing format; if no, do nothing.
- **Model:** Haiku (cheap; this is summarization against a clear format). Upgrade
  to Sonnet only if draft quality proves insufficient.
- **Input:** the structured result object from the triggering stage + the
  relevant run/PR id + the target agent's *current* lessons file (so it can
  avoid duplicating an existing rule).
- **Output:** a single lesson entry appended to `req2prod/lessons/<agent>.md`,
  using the exact `## <ID> — <title>` / Source / Severity / Trigger / Rule
  format already in that store. Plus a one-line rationale for the PR body.
- **Dedup:** if the outcome is already covered by an existing lesson, it appends
  nothing and says so. If a file would exceed ~15 entries, it proposes a
  consolidation instead of a 16th entry.

## The gated flow

```
notable outcome
   → retrospective agent drafts a candidate lesson (or decides "no lesson")
   → opens a PR: branch `lesson/<agent>-<runid>`, appends to lessons/<agent>.md,
     title "[lesson] <agent>: <short rule>"
   → normal review runs (code_reviewer) + pr_arbiter / Marco
   → merged only on approval   ← the lesson becomes live behavior here
```

Reuses existing machinery: `create_feature_branch_and_open_pr` for the PR;
the existing Req2Prod pipeline for review. **No auto-merge of self-authored
lessons** — this class of PR always requires an explicit approving review, even
if other PRs auto-merge.

## Injection & drift safety (why this is safe)

- The retrospective reads logs/diffs that *could* contain adversarial text, but
  (a) it only ever emits a lesson-format entry, and (b) that entry is
  human/arbiter-reviewed before it can change any agent's behavior. A poisoned
  outcome cannot silently persist.
- Every lesson is diff-visible and attributable, so drift is auditable and
  reversible (retiring a lesson is also a reviewed PR).

## Where it hooks in

Two options for *how* the retrospective is invoked:

- **A. Inline in the runners** (recommended for v1): the existing runners
  (`code_review_runner`, the rollback path, the devops breaker) call the
  retrospective agent when their own notable-outcome condition is met. Simplest;
  no new workflow.
- **B. A dedicated workflow** triggered on specific events. More decoupled, more
  moving parts. Defer unless A proves awkward.

## Rollout phases

| Phase | Deliverable | Risk |
|---|---|---|
| 3a | Retrospective agent drafts a lesson to a **file/artifact**; Marco opens the PR manually | minimal — nothing auto-acts |
| 3b | Agent **auto-opens** the PR (still human/arbiter-gated merge) | low — merge still gated |
| 3c | (optional) consolidation pass when a lessons file grows too large | low |

Start with **3a** — it proves the drafting quality with zero automation risk,
then promote to 3b once the drafts are trusted.

## Open decisions for Marco

1. **3a first (draft-to-file), or straight to 3b (auto-open PR)?**
   Recommendation: 3a first.
2. **Which triggers to start with?** Recommendation: the two highest-signal,
   lowest-ambiguity ones — **rollback fired** and **devops circuit-breaker
   tripped** — then expand.
3. **Who approves lesson PRs — `pr_arbiter`, or always you?** Recommendation:
   `pr_arbiter` may approve, but self-authored-lesson PRs are never auto-merged
   without a human glance for the first N.
4. Retrospective model: **Haiku** to start (cheap), upgrade only if needed.

## Success criteria

- A rollback or a breaker trip produces a clear, correctly-targeted draft lesson
  that a reviewer would actually accept.
- No lesson ever reaches `main` without an approving review.
- The store stays curated (dedup + consolidation), not an ever-growing pile.
