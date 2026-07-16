# Splitting the admin console out of the Job Finder app

## The problem

Watching a deploy in the SDLC view kills the SDLC view.

`streamlit_app.py` serves both the public Job Finder and the admin console —
the console is the same script behind `?admin=1`. One script, one Streamlit
process, one systemd unit:

```
jobfinder.service -> streamlit run streamlit_app.py --server.port 8501
```

The deploy restarts that unit whenever the diff touches any `.py` file:

```yaml
if echo "$CHANGED" | grep -qE '\.py$|^requirements\.txt$'; then
  needs_restart=true
```

So shipping a Job Finder change while watching it go through the pipeline
restarts the process rendering the pipeline. The view you are watching *with*
is the thing being restarted. A change touching only `req2prod/` restarts the
public app too, for the same reason.

## What we're building

Two processes from one checkout, and a deploy that restarts only the one whose
code actually changed.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Where the console lives | `req2prod.nl/app` | Its own product, its own domain. req2prod.nl already resolves to this box and serves the static site. |
| What moves | The whole admin console — all four tabs | Cuts along the boundary that already exists (`?admin=1`) rather than inventing a new one. |
| Restart rule | Allowlist per service; anything unknown or mixed restarts both | An unclassified file degrades to today's behaviour, so it can never leave a service running stale code. |
| Old `?admin=1` URL | Stops working | The query param disappears from the code rather than being special-cased. Bookmark gets updated by hand. |

### Rejected: classifying by `PRODUCT_PATHS`

`cto_cockpit_admin.py` already maps top-level paths to products, and reusing it
for the restart rule looks tempting — one source of truth, already tested.

It's the wrong axis. `PRODUCT_PATHS` answers *which product owns this file*;
the restart rule needs *which process imports this file*. `jobfinder_admin.py`
is Job Finder's product but renders inside the admin console, so a
product-based rule would restart the public app and leave the console stale —
exactly backwards.

## Architecture

One checkout, one venv, one `.env`, two units:

```
/home/ubuntu/crewai-starter
├── streamlit_app.py   -> jobfinder.service  :8501   public Job Finder
└── req2prod_app.py    -> req2prod.service   :8502   admin console        [NEW]
```

nginx gains one location and nothing else moves:

```
yourmagicaljobfinder.online/   -> 127.0.0.1:8501       (unchanged)
req2prod.nl/                   -> /var/www/req2prod.nl (static, unchanged)
req2prod.nl/app                -> 127.0.0.1:8502       [NEW]
```

Both processes serve over `127.0.0.1` only, reachable through nginx and its
TLS — the console must not be exposed on plain HTTP, since it carries a
password.

## The code split

`req2prod_app.py` is a new entry point containing what currently sits inside
`if st.query_params.get("admin") is not None:` — `st.set_page_config`, the
Secrets Manager password gate, and the four tabs.

`streamlit_app.py` loses:

- the whole `if st.query_params.get("admin")` block
- its three admin imports (`cto_cockpit_admin`, `jobfinder_admin`,
  `req2prod.admin_ui`)
- `get_admin_password`, `ADMIN_SECRET_NAME`, `AWS_REGION`, and the `boto3`
  import — all four exist solely to serve the admin password gate. Verified:
  `boto3` and `AWS_REGION` appear nowhere else in the file, and `notify.py`
  carries its own `AWS_REGION` for SES.

`?admin=1` then stops working because nothing reads it any more, not because
anything rejects it.

`auth.py` and `reporting.py` stay shared and are imported by both processes —
`jobfinder_admin.py` needs `delete_user`/`set_user_password`, the public app
needs `AuthManager`.

`_run_with_retry` is already duplicated between `streamlit_app.py` and
`req2prod/admin_ui.py`, with a comment explaining why. That stays as it is;
deduping it is not this change's job.

## The restart rule

A Python helper the workflow calls, so the lists are one testable thing rather
than bash spread through YAML:

```
ADMIN_ONLY   req2prod/, req2prod_*.py, cto_cockpit_*.py, jobfinder_admin.py
PUBLIC_ONLY  streamlit_app.py, job_search.py, ai_viewer.py, config/, assets/
otherwise    both
```

`req2prod_*.py` covers the new `req2prod_app.py` without naming it. `ai_viewer.py`
is `PUBLIC_ONLY` on evidence, not assumption: the admin block doesn't call
`setup_layout` or `render_sidebar_toggle`.

`otherwise` covers `auth.py`, `reporting.py`, `notify.py`, `requirements.txt`,
any new top-level file, and any diff spanning both lists. Restarting both is
what happens today, so the unknown case is never a regression — only a missed
optimisation.

The helper takes a list of changed paths and returns the set of services to
restart. The workflow calls it once and gates each restart step on the result.

## Migration order

The obvious sequencing breaks. If the workflow starts restarting
`req2prod.service` before that unit exists on the box, every deploy goes red.
`infra/` is the source of truth for nginx and systemd but nothing applies it —
those have always been operator steps, and the deploy has only ever restarted
a unit, never installed one.

**PR 1 — additive, changes nothing at runtime**

- add `req2prod_app.py`
- add `infra/req2prod.service`
- add the `/app` location to `infra/nginx-req2prod.conf`
- fix `infra/README-site.md` (below)

`streamlit_app.py` still serves the admin, the workflow still restarts one
service. Nothing observable changes.

**Operator step — by hand, over SSH**

Install the unit, add the nginx location, reload. Now `req2prod.nl/app` works
*and* `?admin=1` still works. Both routes live; nothing has been taken away
yet, so this is verifiable before it matters.

**PR 2 — the cut**

- remove the admin block from `streamlit_app.py`
- switch the workflow to the new restart rule

At no point is the admin console unreachable.

## Testing

- Unit tests for the classifier: a diff in, a set of services out. Cover
  admin-only, public-only, mixed, unknown file, `requirements.txt`.
- `AppTest` that `req2prod_app.py` renders the four tabs.
- `AppTest` that `streamlit_app.py` has no admin path left — `?admin=1`
  renders the ordinary public page.
- The real proof, after PR 2: deploy a `job_search.py`-only change and watch
  the SDLC view stay alive through it.

## Risks

**The operator step is the failure point.** If it's skipped, PR 2 makes the
console unreachable and the deploy red. Mitigated by ordering: PR 2 doesn't
merge until `req2prod.nl/app` is confirmed serving.

**Both processes share one `.env` and one venv.** A `requirements.txt` change
restarts both, which the rule already handles. A `.env` change is invisible to
the diff and always has been — unchanged by this work.

**Two processes, two sets of Streamlit session state.** Nothing is shared
between them today either (the admin console and the public app are separate
sessions already, in the same process), so this changes nothing in practice.

## Also in PR 1: `infra/README-site.md` is stale

It opens with "**Not live yet.** ... `req2prod.nl` does not resolve to this
box yet: DNS has not been pointed at it, the nginx server block for it does not
exist, and no TLS certificate has been issued."

All three have since landed — `https://req2prod.nl` serves the real site from
`16.171.202.23` with valid TLS. The doc also warns against editing
`site/index.html`, which is now wrong and would mislead anyone who reads it.
Fixed as part of PR 1, since that PR is already in the file adding `/app`.

## Out of scope

- Deduping `_run_with_retry`.
- Moving `terraform.tfstate` to an S3 backend.
- Any change to how `site/` is published.
- Making the deploy install systemd units (it stays an operator step).
