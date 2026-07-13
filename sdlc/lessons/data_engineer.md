# Lessons — data_engineer

## DE-01 — Confirm no real data or PII is committed, in tree or history
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** reviewing data handling.
- **Rule:** check the real tracked-files list (not just disk) for any database file,
  data export, or per-user data directory actually committed to git. Use
  `git_file_history` on anything that could contain real records to see if it's in
  history even if gitignored now. A leaked user's data or credentials is a blocking
  finding, not a nitpick.

## DE-02 — Judge structure around the data
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** assessing data practices.
- **Rule:** the app persists auth records in SQLite and stores generated
  resumes/user files per-account under `users/<email>/`. Judge whether config and
  schema are kept separate from code and whether there's any real data validation,
  versus scripts that just touch data with no structure around it.
