# Lessons — python_lead_engineer

## PL-01 — Judge against the real tracked-files list, not what's on disk
- **Source:** operating principle
- **Severity:** hygiene
- **Trigger:** checking repo hygiene.
- **Rule:** check whether build artifacts like `venv/`, `__pycache__/`, `.pyc` files,
  or `.DS_Store` are actually *committed* — using the real tracked-files list, not
  merely what's present on disk.

## PL-02 — Judge code quality from real files only
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** reviewing Python quality.
- **Rule:** open actual source files, not just filenames, and judge: sensible
  package/module structure, type hints, a real pinned dependency manifest, presence of
  linting/formatting config (ruff/black/mypy), any tests at all, idiomatic error
  handling instead of bare `except:` or leftover print-debugging. Only flag concrete
  problems seen in real files, never hypothetical ones.
