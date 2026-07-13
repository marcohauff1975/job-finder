# Lessons — software_architect

## SA-01 — Decide fits-existing vs needs-new-infra explicitly
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** turning requirements into technical direction.
- **Rule:** for each feature, state plainly whether it fits the existing app as-is or
  needs new infrastructure/a separate service — and if so, what and why. The app is a
  single Streamlit process, CrewAI agents in YAML wired in Python, SQLite auth,
  per-user `users/<email>/` storage, manual deploy to one server.

## SA-02 — Apply this app's real non-functional constraints
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** specifying NFRs for a feature.
- **Rule:** background/scheduled work can't live inside a Streamlit request-response
  cycle. Anything touching `auth.py` or the DB schema must stay backward compatible
  with existing user records (same as code_reviewer checks). Anything writing shared
  state must consider concurrent sessions.

## SA-03 — Name genuine technical ambiguity instead of guessing
- **Source:** operating principle
- **Severity:** process
- **Trigger:** the PM's requirements leave something technically ambiguous.
- **Rule:** say so explicitly as a clarification needed, rather than picking an
  assumption and hoping. Output engineering-ready direction concrete enough that the
  engineer knows exactly what to build before writing a line — not architectural
  philosophy.
