# Lessons — code_reviewer

## CR-01 — Verify unfamiliar versions and model ids before flagging them
- **Source:** 2026-07 · PR #18
- **Severity:** false-positive
- **Trigger:** about to flag a pinned dependency version in `requirements.txt`, or
  an `LLM(model=...)` / `agent_models.json` model id, as implausible or nonexistent
  because it looks unfamiliar.
- **Rule:** your training has a cutoff, so a genuinely new package version or Claude
  model id is indistinguishable by pattern from a typo. Call
  `check_pypi_package_version` / `check_anthropic_model_id` first, and only flag it
  if the tool itself confirms it does not exist. PR #18 requested changes on a
  version and a model id that were both already real and already running here.

## CR-02 — Watch for the failure classes specific to this app
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** reviewing any diff.
- **Rule:** this is a Streamlit + CrewAI app with SQLite-backed auth, per-user
  storage under `users/<email>/`, and manual deploy to one prod server. Specifically
  watch for: shared state written without regard to concurrent sessions; secrets or
  credentials committed in plaintext; changes to `auth.py` or the DB schema that
  aren't backward compatible with existing user data; and deploy/infra files that
  could overwrite runtime data (`data/`, `users/`, `.env`) if synced carelessly.

## CR-03 — Flag only concrete, line-tied problems
- **Source:** operating principle
- **Severity:** process
- **Trigger:** tempted to note a style preference or a hypothetical.
- **Rule:** only flag concrete problems tied to specific lines that affect
  correctness or safety. Do not invent hypothetical issues or nitpick style that
  doesn't affect either.
