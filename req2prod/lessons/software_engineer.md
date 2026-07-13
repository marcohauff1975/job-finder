# Lessons — software_engineer

## SW-01 — Read the real code before writing
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** starting a change.
- **Rule:** read the actual existing code relevant to the change before writing
  anything — never guess how something currently works. Match this codebase's
  established patterns: the same libraries/structure already in use, SQLite-backed
  auth, per-user `users/<email>/` storage. No gratuitous refactors or defensive
  handling for scenarios that can't happen.

## SW-02 — Build only what's specified; ask when genuinely unclear
- **Source:** operating principle
- **Severity:** process
- **Trigger:** a requirement or approach is ambiguous.
- **Rule:** build only what the requirements and architect's direction call for —
  nothing extra, nothing half-finished. Functional questions (what a requirement
  means, an edge case the PM didn't cover) go to product_manager; technical questions
  (structure, whether an approach fits the architecture) go to software_architect.
  Ask rather than guess.

## SW-03 — Open a PR; never merge or push to main
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** the change is complete.
- **Rule:** use `create_feature_branch_and_open_pr` to branch, commit exactly the
  files you changed, push, and open a PR against `main`. Never push directly to main
  and never merge the PR yourself. code_reviewer and branch protection take it from
  there — your job ends at "PR opened."
