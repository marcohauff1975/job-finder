# Agent skills

Vendored [Agent Skills](https://code.claude.com/docs/en/skills) (`SKILL.md` +
reference files) that the Req2Prod review agents load as domain knowledge. Each
skill is attached to the relevant agent in `Req2Prod.py` via `Agent(skills=...)`
and injected into that agent's system prompt at runtime.

They are vendored here (rather than referenced from a developer's personal
`~/.claude/skills/`) so the pipeline has them wherever it runs — CI and prod —
reproducibly and version-pinned.

## What's attached where

| Skill | Attached to | Purpose |
|---|---|---|
| `modern-python` | `python_lead_engineer`, `ai_engineer` | Modern Python tooling & conventions (uv, ruff, ty, pyproject) |
| `insecure-defaults` | `security_engineer` | Detecting fail-open insecure defaults |
| `frontend-design` | `ux_reviewer` | Distinctive, intentional UI design judgment |
| `brand-guidelines` | `ux_reviewer` | Applying a consistent brand system |

## Attribution & licenses

These are third-party skills, vendored verbatim (no modifications). They keep
their upstream licenses; this project's own MIT license does not apply to them.

- **`frontend-design`, `brand-guidelines`** — © Anthropic, from
  [anthropics/skills](https://github.com/anthropics/skills), licensed
  **Apache-2.0** (see each folder's `LICENSE.txt`).
- **`modern-python`, `insecure-defaults`** — © Trail of Bits, from
  [trailofbits/skills](https://github.com/trailofbits/skills), licensed
  **CC BY-SA 4.0** (https://creativecommons.org/licenses/by-sa/4.0/). Vendored
  unmodified with attribution as required by that license.
