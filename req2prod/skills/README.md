# Agent skills

Vendored [Agent Skills](https://code.claude.com/docs/en/skills) (`SKILL.md` +
reference files) intended for the Req2Prod review agents to load as domain
knowledge. **Not yet wired up:** no `Agent()` definition in `Req2Prod.py`
currently passes a `skills=` argument or otherwise references this directory,
so these files are not presently attached to or injected into any agent's
system prompt. The table below describes the intended mapping once wiring is
added.

They are vendored here (rather than referenced from a developer's personal
`~/.claude/skills/`) so the pipeline has them wherever it runs — CI and prod —
reproducibly and version-pinned.

## What's intended to attach where

| Skill | Intended for | Purpose |
|---|---|---|
| `modern-python` | `python_lead_engineer`, `ai_engineer` | Modern Python tooling & conventions (uv, ruff, ty, pyproject) |
| `insecure-defaults` | `security_engineer` | Detecting fail-open insecure defaults |
| `frontend-design` | `ux_reviewer` | Distinctive, intentional UI design judgment |
| `brand-guidelines` | `ux_reviewer` | Applying a consistent brand system |

## Attribution & licenses

Only each skill's `SKILL.md` (its instructions) is vendored, verbatim and
unmodified — that is the only part injected into an agent's prompt at runtime.
The skills' optional `references/` reference files are intentionally omitted (an
agent never loads them here); the full skills live upstream at the links below.
These are third-party skills that keep their upstream licenses; this project's
own MIT license does not apply to them.

- **`frontend-design`, `brand-guidelines`** — © Anthropic, from
  [anthropics/skills](https://github.com/anthropics/skills), licensed
  **Apache-2.0** (see each folder's `LICENSE.txt`).
- **`modern-python`, `insecure-defaults`** — © Trail of Bits, from
  [trailofbits/skills](https://github.com/trailofbits/skills), licensed
  **CC BY-SA 4.0** (https://creativecommons.org/licenses/by-sa/4.0/). Vendored
  unmodified with attribution as required by that license.
