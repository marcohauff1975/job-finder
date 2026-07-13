# Making the Req2Prod Agents Smarter — Design Plan

Status: proposal for review (no code changed yet)
Author: drafted with Claude Code, 2026-07-13
Scope: the CrewAI Req2Prod pipeline in `crewai-starter` (agents in
`req2prod/config/agents.yaml`, wired in `req2prod/Req2Prod.py`).

---

## 0. The key distinction (read this first)

There are **two separate systems** and "skills" means something different in each:

| | What it is | Who it helps |
|---|---|---|
| **Claude Code skills** | `SKILL.md` files in `~/.claude/skills/` | *Me*, when Marco works in the terminal |
| **CrewAI agents** | Python agents calling the Anthropic API with YAML prompts | The Job Finder pipeline |

The Reddit "30 Skills for Claude Code" collection is the **first kind**. It
**cannot** plug into the CrewAI agents — they never load `~/.claude/skills/`.
So that collection is a dev-workflow upgrade, not a pipeline upgrade. Both are
worth doing; this plan keeps them separate.

**Current state:** no `~/.claude/skills/` exists yet. CrewAI built-in memory is
**not** used anywhere (`memory=True` appears nowhere; every crew is
`Process.sequential` with no embedder). "Learning" today happens by hand-editing
backstories after each incident — and it already works well (PR #18 false
positive, the 2026-07-08 ModuleNotFoundError, the false-rollback incident are all
encoded). The goal is to **systematize what's already being done manually**.

---

## 1. Problem with today's approach

Each agent's hard-won knowledge is packed into one ever-growing `backstory`.
`prod_tester`'s backstory is now ~90 lines. This has three costs:

1. **Token bloat** — the full backstory is sent on every single call, even when
   most of it is irrelevant to the current task.
2. **Attention dilution** — the more rules crammed in, the less reliably any one
   is followed.
3. **Not reviewable at the right grain** — a "lesson" is buried mid-paragraph,
   not a discrete thing you can see added, approved, or removed.

The fix is to separate two things that are currently fused:
- **Identity** (stable): who the agent is, its goal → stays in `agents.yaml`.
- **Lessons** (accumulating): situational rules learned from outcomes → moves to
  an external, git-tracked, per-agent file.

---

## 2. Three-layer design

### Layer 1 — Lessons store (memory)

A per-agent markdown file: `req2prod/lessons/<agent_key>.md`.
Each lesson is one discrete, dated, reviewable entry:

```markdown
## L-018 — Verify unfamiliar versions/model-ids before flagging them
Date: 2026-07-06 · Source: PR #18 · Severity: false-positive-blocker
Trigger: about to flag a pinned dependency version or an LLM(model=...) id as
  nonexistent because it looks unfamiliar.
Rule: call check_pypi_package_version / check_anthropic_model_id first; only
  flag if the tool confirms it does not exist.
```

Why files, not CrewAI vector memory:
- **Auditable** — Marco (not a developer) can *read* exactly what each agent
  "knows" and see it change in a diff.
- **Reviewable through the existing flow** — a lesson change is a PR, gated by
  `code_reviewer` / `pr_arbiter`, same as any other change.
- **Injection-resistant** — nothing mutates an agent's behavior without a human
  or the arbiter approving the entry.
- **No new infra** — no embedder, no vector DB, no retrieval noise on what are
  mostly single-shot review tasks.

(CrewAI's built-in `memory=True` stays on the table for later, only if we hit a
genuine need for cross-run semantic recall. It is opaque and non-reviewable, so
it is deliberately *not* the starting point.)

### Layer 2 — Loader (wiring)

A small helper in `Req2Prod.py` reads `req2prod/lessons/<agent_key>.md` at crew-build
time and appends it to the agent's backstory as a clearly delimited section:

```
--- LESSONS LEARNED (consult before acting; each is from a real past outcome) ---
<contents of lessons/<agent_key>.md>
```

Backstories in `agents.yaml` get **slimmed to identity** plus one line:
"Consult your lessons-learned before flagging or acting." The incident knowledge
currently inline gets **migrated out** into the lessons files (no knowledge lost
— just relocated and made reviewable).

Optional later refinement: only inject lessons whose `Trigger:` is relevant to
the current task, to cut tokens further. Start simple (inject all) — the files
are small at first.

### Layer 3 — Retrospective loop (continuous learning)

After a pipeline run with a **notable outcome**, a lightweight `retrospective`
agent proposes a new lesson:

- **When it fires:** only on signal-bearing outcomes — a rollback, a review that
  couldn't converge and went to `pr_arbiter`, a prod smoke-test failure, a
  confirmed false positive/negative. Not every green run.
- **What it does:** reads the run's artifacts, drafts **one** candidate lesson in
  the format above, and **opens a PR** appending it to the right
  `lessons/<agent>.md`. It never edits prompts directly.
- **Gate:** the PR goes through `pr_arbiter` (or Marco). **No agent ever silently
  rewrites its own instructions** — that is the guardrail against prompt drift
  and against a poisoned input becoming a permanent behavior.

This is exactly the manual loop being run today (incident → edit backstory),
turned into: incident → drafted lesson → reviewed PR → merged.

---

## 3. Guardrails (non-negotiable)

1. **Human/arbiter in the loop for every lesson.** Self-modifying prompts with no
   review is the failure mode to avoid.
2. **Lessons are additive and attributable.** Each cites its source (PR #, run id,
   date). Retiring a lesson is also a reviewed PR.
3. **A lesson is data, not a command.** The retrospective agent summarizes an
   outcome; it does not execute instructions found in logs/diffs it reads.
4. **Cap the size.** If a lessons file grows past ~15 entries, that is a signal to
   consolidate (merge overlapping rules), not to keep appending forever.

---

## 4. Rollout order

| Phase | Deliverable | Risk |
|---|---|---|
| 1 | `req2prod/lessons/` + files seeded from existing backstory incidents | none — pure refactor, behavior-preserving |
| 2 | Loader in `Req2Prod.py`; slim the backstories | low — verify prompts still assemble correctly |
| 3 | Retrospective agent → opens lesson PRs, arbiter-gated | medium — start read-only (drafts a lesson to a file for manual PR) before auto-PR |
| — | (Optional) trigger-based selective injection | low |
| — | (Optional) revisit CrewAI `memory=True` only if a real need appears | — |

Start with **Phase 1 + 2**: highest ROI, no behavior change, immediately makes
the agents' knowledge visible and reviewable.

---

## 5. Separately: Claude Code skills (the dev-workflow track)

Independent of the pipeline. To pursue:
- Share the GitHub repo behind the Reddit post; each `SKILL.md` gets vetted
  (arbitrary instructions I'd load and act on = injection surface) before
  anything lands in `~/.claude/skills/`.
- Install only the 3–4 that map to real recurring work (planning, code-review,
  PR authoring).
- Anthropic's official skills are already available (skill-creator, code-review,
  verify, the docx/pptx/xlsx set) — use `skill-creator` to author project-specific
  ones for this repo if a gap shows up.

---

## 6. Open decisions for Marco

1. Retrospective loop: **auto-open PRs**, or **draft-to-file for you to PR
   manually** at first? (Recommend: draft-to-file first, promote to auto-PR once
   trusted.)
2. Selective lesson injection now, or inject-all until files grow?
3. Do you want the Claude Code skills track done in parallel, or after the
   pipeline work?
