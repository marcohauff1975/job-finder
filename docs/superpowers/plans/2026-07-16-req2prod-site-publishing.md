# Req2Prod Site Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Req2Prod product pages to req2prod.nl so that adding a page is "add the file, merge, deploy" — no nginx edit, no workflow edit, no SSH.

**Architecture:** A `site/` tree in the repo *is* the site; path in repo equals path on the web. One nginx server block serves req2prod.nl from `/var/www/req2prod.nl` with `root` + `try_files` — on its own domain there is no Streamlit proxy to dodge, so no per-page config exists. Deploy runs a single guarded script that `rsync --delete`s the tree, so no deploy step ever names a page. The old `yourmagicaljobfinder.online/req2prod` becomes a 301.

**Tech Stack:** nginx, certbot/Let's Encrypt, bash, rsync, GitHub Actions, AWS Lightsail (Ubuntu 24.04).

**Spec:** `docs/superpowers/specs/2026-07-16-req2prod-site-publishing-design.md`

## Global Constraints

- **Verification greps page content. Never assert only a status code.** A `200`
  from this box can mean Streamlit's catch-all answered instead of the page —
  this happened on 2026-07-16 and a status-only check reported success while the
  page was not live.
- **Deploys are operator-triggered.** Never run `gh workflow run "Deploy to
  production"`, never SSH in and deploy. Prepare the change; hand Marco the exact
  step. This is a hard rule on this box.
- **Never commit video or other large binaries.** The repo is public and git
  history is permanent. Video is embedded via `<iframe>`.
- **`nginx -t` before every reload.** A failed test changes nothing; the old
  config keeps serving. Use `reload` (graceful), never `restart`.
- **The repo is public.** Never write production vulnerability details, secrets,
  or file modes of sensitive files into any tracked file, commit message, or PR
  body.
- **Never `chmod o+x /home/ubuntu`.** The web server must not gain traverse into
  the app user's home directory. That constraint is why the site is served from
  `/var/www`.
- Static IP of the box: `16.171.202.23`. Server user: `ubuntu`. App dir on box:
  `/home/ubuntu/crewai-starter`. Lightsail instance `job-finder`, region
  `eu-north-1`.

  These are recorded deliberately, not leaked. They are already tracked in this
  repo — the IP is the `server_name` in `infra/nginx-jobfinder.conf`, and the
  instance, region, user and app dir are env vars in
  `.github/workflows/deploy-to-prod.yml` — and the IP is what
  `dig yourmagicaljobfinder.online` returns to anyone who asks. They cannot be
  removed from those files without breaking nginx and the deploy. A public web
  server's address is not a secret, and redacting it here while two other tracked
  files publish it would buy nothing and leave this plan unrunnable. What must
  never appear in this public repo is genuinely non-public detail: credentials,
  secrets, and the specific weaknesses of a live system.

## Operator Gate

**Tasks 3 and 5 are manual operator steps performed by Marco.** They involve DNS
changes at one.com, certbot, and production reloads. No agent may perform them.
An agent reaching Task 3 must stop, present the commands, and wait.

Task order is a hard dependency chain: certbot cannot issue a certificate until
DNS resolves to the box, and the 301 cannot be tested until its target exists.

## Rollback

Nothing in this plan is one-way. If a step fails, back out that step — do not
push forward hoping the next one fixes it.

| If this fails | Roll back by |
|---|---|
| DNS (Task 3 Step 4) | Revert both A records to `46.30.211.38` at one.com |
| Server block (Task 3 Step 6) | `sudo rm /etc/nginx/sites-enabled/req2prod`, `sudo nginx -t`, `sudo systemctl reload nginx` |
| certbot (Task 3 Step 8) | `sudo certbot delete --cert-name req2prod.nl`, then remove the server block as above |
| Site content (Task 3 Step 7) | `git revert` the content commit, redeploy — rsync mirrors the revert |
| The 301 (Task 4) | Restore the file-serving block in `infra/nginx-jobfinder.conf`, re-copy, `nginx -t`, reload. **Do not run Task 4 Step 5** (`rm -rf /var/www/req2prod`) until the redirect is verified — that directory is the rollback target |

A failed `nginx -t` has already rolled itself back: the old config is still
serving and nothing changed. Never reload on a failed test.

The job-finder app is untouched by every task here except Task 4, which edits the
shared `nginx-jobfinder.conf`. If Task 4 breaks the app, restoring the previous
block and reloading is sufficient — no `jobfinder.service` restart is involved
anywhere in this plan, so live Streamlit sessions are never dropped.

---

### Task 1: Add the guarded site-sync script

Both deploy workflows need identical sync logic. Putting it in one script keeps it
DRY and makes it testable without running a deploy.

**Files:**
- Create: `infra/sync-site.sh`
- Test: `tests/test_sync_site.py`

**Interfaces:**
- Produces: `infra/sync-site.sh <repo-dir>` — syncs `<repo-dir>/site/` to
  `/var/www/req2prod.nl/`. Exits non-zero without touching the destination if the
  source is missing or empty. Called by both workflows in Task 2 as
  `sudo <repo-dir>/infra/sync-site.sh <repo-dir>`.

- [ ] **Step 1: Write the failing test**

`rsync --delete` against an empty source silently wipes the destination. That is
the failure this script exists to prevent, so it is the behaviour we test first.

Create `tests/test_sync_site.py`:

```python
"""Tests for infra/sync-site.sh.

rsync --delete mirrors its source, so syncing a missing or empty site/ would
wipe the live web root. These tests pin that the script refuses instead.

DEST_OVERRIDE keeps the tests out of the real /var/www.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "sync-site.sh"


def run_sync(repo_dir, dest):
    return subprocess.run(
        ["bash", str(SCRIPT), str(repo_dir)],
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "DEST_OVERRIDE": str(dest)},
        capture_output=True,
        text=True,
    )


def test_missing_site_dir_is_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    dest = tmp_path / "dest"

    result = run_sync(repo, dest)

    assert result.returncode != 0
    assert not dest.exists()


def test_empty_site_dir_does_not_wipe_published_site(tmp_path):
    repo = tmp_path / "repo"
    (repo / "site").mkdir(parents=True)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "index.html").write_text("live page")

    result = run_sync(repo, dest)

    assert result.returncode != 0
    assert (dest / "index.html").read_text() == "live page"


def test_syncs_the_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / "site" / "main").mkdir(parents=True)
    (repo / "site" / "main" / "index.html").write_text("<title>Req2Prod — Overview</title>")
    dest = tmp_path / "dest"

    result = run_sync(repo, dest)

    assert result.returncode == 0
    assert (dest / "main" / "index.html").read_text() == "<title>Req2Prod — Overview</title>"


def test_removed_page_is_unpublished(tmp_path):
    repo = tmp_path / "repo"
    (repo / "site" / "main").mkdir(parents=True)
    (repo / "site" / "main" / "index.html").write_text("page")
    dest = tmp_path / "dest"

    run_sync(repo, dest)
    orphan = dest / "orphan.html"
    orphan.write_text("stale")

    run_sync(repo, dest)

    assert not orphan.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_site.py -v`

Expected: FAIL — all four tests fail because `infra/sync-site.sh` does not exist
yet (bash exits 127, so `returncode != 0` passes for the two refusal tests but
`test_syncs_the_tree` and `test_removed_page_is_unpublished` fail).

- [ ] **Step 3: Write minimal implementation**

Create `infra/sync-site.sh`:

```bash
#!/usr/bin/env bash
# Sync the req2prod.nl static site from the repo checkout to the web root.
#
# nginx serves req2prod.nl from /var/www/req2prod.nl, not from the checkout, so
# `git pull` alone does not update the served site. Both deploy workflows call
# this immediately after pulling.
#
# Usage: sudo infra/sync-site.sh <repo-dir>
set -euo pipefail

REPO_DIR="${1:?usage: sync-site.sh <repo-dir>}"
SRC="$REPO_DIR/site"
DEST="${DEST_OVERRIDE:-/var/www/req2prod.nl}"

# rsync --delete mirrors the source. Against a missing or empty source it would
# wipe the live site, so refuse rather than publish nothing.
if [ ! -d "$SRC" ]; then
  echo "refusing to sync: $SRC does not exist" >&2
  exit 1
fi
if [ -z "$(ls -A "$SRC")" ]; then
  echo "refusing to sync: $SRC is empty (--delete would wipe $DEST)" >&2
  exit 1
fi

mkdir -p "$DEST"
rsync -a --delete "$SRC/" "$DEST/"
echo "synced $SRC -> $DEST"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `chmod +x infra/sync-site.sh && python -m pytest tests/test_sync_site.py -v`

Expected: PASS — `4 passed`.

- [ ] **Step 5: Commit**

The script must be committed executable (mode 100755) — the workflows invoke it
directly. Confirm with `git ls-files -s infra/sync-site.sh` after adding; it must
show `100755`, not `100644`.

```bash
git add infra/sync-site.sh tests/test_sync_site.py
git commit -m "Add guarded site sync script for req2prod.nl

rsync --delete mirrors the source, so an empty or missing site/ would wipe
the live web root. The script refuses to sync in that case rather than
publishing nothing. Both deploy workflows will call it.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Move content to site/ and switch both workflows to the sync script

**Files:**
- Create: `site/main/index.html` (moved from `static/req2prod/index.html`)
- Delete: `static/req2prod/index.html`
- Delete: `infra/README-req2prod.md` (replaced by `infra/README-site.md`)
- Create: `infra/README-site.md`
- Modify: `.github/workflows/deploy-to-prod.yml` (the `Sync the req2prod one-pager to /var/www` step)
- Modify: `.github/workflows/req2prod-pipeline.yml` (same step)

**Interfaces:**
- Consumes: `infra/sync-site.sh <repo-dir>` from Task 1.
- Produces: `site/main/index.html` served at `req2prod.nl/main` once Task 3 lands.

**Safety note:** after this task deploys, `/var/www/req2prod/index.html` remains on
disk unmanaged, so `yourmagicaljobfinder.online/req2prod` keeps serving the page,
frozen. That is intentional — the old URL stays up until Task 4 flips it to a 301.
Do not delete that directory yet.

**This task touches `.github/workflows/`.** The reviewer bot's PAT lacks `workflow`
scope and cannot push fixes to those files; if `code_review` fails on a push error
rather than a finding, that is why.

- [ ] **Step 1: Move the page**

```bash
mkdir -p site/main
git mv static/req2prod/index.html site/main/index.html
```

- [ ] **Step 2: Verify the move kept the file intact**

Run: `grep -c "Req2Prod — Overview" site/main/index.html && wc -c < site/main/index.html`

Expected: `1` then `29559`. A different byte count means the file changed — stop
and investigate rather than continuing.

- [ ] **Step 3: Replace the sync step in `deploy-to-prod.yml`**

Find the existing step (added by #64):

```yaml
      # nginx serves /req2prod from /var/www, not from the checkout, so the pull
      # above does not update the page that is actually served. Without this the
      # checkout advances and /var/www silently keeps serving the old one-pager.
      - name: Sync the req2prod one-pager to /var/www
        run: |
          ssh -i /tmp/deploy_key "$REMOTE_USER@$INSTANCE_IP" \
            "sudo mkdir -p /var/www/req2prod && sudo cp $REMOTE_APP_DIR/static/req2prod/index.html /var/www/req2prod/index.html"
```

Replace it with:

```yaml
      # nginx serves req2prod.nl from /var/www, not from the checkout, so the
      # pull above does not update the served site. The script guards against
      # syncing an empty tree, which --delete would turn into a wipe.
      - name: Sync the req2prod.nl site to /var/www
        run: |
          ssh -i /tmp/deploy_key "$REMOTE_USER@$INSTANCE_IP" \
            "sudo $REMOTE_APP_DIR/infra/sync-site.sh $REMOTE_APP_DIR"
```

- [ ] **Step 4: Replace the sync step in `req2prod-pipeline.yml`**

Same replacement, but this workflow's ssh calls include
`-o StrictHostKeyChecking=accept-new`. Find:

```yaml
      # nginx serves /req2prod from /var/www, not from the checkout, so the pull
      # above does not update the page that is actually served. Without this the
      # checkout advances and /var/www silently keeps serving the old one-pager.
      - name: Sync the req2prod one-pager to /var/www
        run: |
          ssh -o StrictHostKeyChecking=accept-new -i /tmp/deploy_key "$REMOTE_USER@$INSTANCE_IP" \
            "sudo mkdir -p /var/www/req2prod && sudo cp $REMOTE_APP_DIR/static/req2prod/index.html /var/www/req2prod/index.html"
```

Replace with:

```yaml
      # nginx serves req2prod.nl from /var/www, not from the checkout, so the
      # pull above does not update the served site. The script guards against
      # syncing an empty tree, which --delete would turn into a wipe.
      - name: Sync the req2prod.nl site to /var/www
        run: |
          ssh -o StrictHostKeyChecking=accept-new -i /tmp/deploy_key "$REMOTE_USER@$INSTANCE_IP" \
            "sudo $REMOTE_APP_DIR/infra/sync-site.sh $REMOTE_APP_DIR"
```

- [ ] **Step 5: Verify both workflows still parse and reference the script**

Run:

```bash
for f in .github/workflows/deploy-to-prod.yml .github/workflows/req2prod-pipeline.yml; do
  python3 -c "
import yaml
d = yaml.safe_load(open('$f'))
steps = [s.get('name') for j in d['jobs'].values() for s in j.get('steps', [])]
assert 'Sync the req2prod.nl site to /var/www' in steps, 'new step missing in $f'
assert 'Sync the req2prod one-pager to /var/www' not in steps, 'old step still present in $f'
print('OK $f')
"
done
grep -c "sync-site.sh" .github/workflows/deploy-to-prod.yml .github/workflows/req2prod-pipeline.yml
grep -rc "static/req2prod" .github/workflows/ || echo "no stale references"
```

Expected: `OK` for both files, `1` occurrence of `sync-site.sh` in each, and no
remaining `static/req2prod` references in workflows.

- [ ] **Step 6: Replace the README**

Remove the old README and create the new one:

```bash
git rm infra/README-req2prod.md
```

Create `infra/README-site.md` with exactly this content:

````markdown
# req2prod.nl — the product site

Everything under `site/` is published to <https://req2prod.nl>. The tree is the
site: the path in this repo is the path on the web.

```
site/main/index.html   ->  https://req2prod.nl/main
site/flows/index.html  ->  https://req2prod.nl/flows
site/flows/chart.svg   ->  https://req2prod.nl/flows/chart.svg
```

## Publishing a page

Add the file under `site/`, merge to main, deploy. That is the whole process.
No nginx change, no workflow change, no SSH — nginx serves the whole tree from
one block, and the deploy syncs the whole tree.

Assets go beside the page that uses them and are referenced relatively.

## Rules

- **Never commit video or other large binaries.** This repo is public and git
  history is permanent — a committed video is downloaded by every clone forever
  and cannot be removed without rewriting history. Embed video with an
  `<iframe>` (YouTube/Vimeo).
- **Deleting a file unpublishes it.** The deploy syncs with `rsync --delete`, so
  the live site mirrors `site/` exactly.
- **Nothing here is private.** The repo is public and so is the site.

## How it is served

nginx serves `/var/www/req2prod.nl` (`root` + `try_files`), not this checkout —
the web server has no traverse permission into the app user's home directory, and
it should not have any. `infra/sync-site.sh` copies the tree out to the web root
on every deploy; both deploy workflows call it right after pulling.

`/` currently 301s to `/main`. When a real homepage lands at `site/index.html`,
delete that `location = /` block from `infra/nginx-req2prod.conf` and `try_files`
will serve it. `/main` is unaffected.

## Verifying

Check content, never just the status code — a `200` from this box can mean the
Streamlit app answered instead of the page:

```
curl -fsS https://req2prod.nl/main | grep -q 'Req2Prod — Overview' && echo LIVE
```
````

- [ ] **Step 7: Run the sync script test again to confirm nothing regressed**

Run: `python -m pytest tests/test_sync_site.py -v`

Expected: `4 passed`.

- [ ] **Step 8: Commit**

`git mv` (Step 1) and `git rm` (Step 6) already staged the move and the deletion,
so this only needs to add the new and modified files.

```bash
git add site/main/index.html infra/README-site.md .github/workflows/deploy-to-prod.yml .github/workflows/req2prod-pipeline.yml
```

Confirm the staged set is exactly what you expect before committing — one rename,
one deletion, one new file, two modified workflows:

```bash
git status --short
```

Expected:

```
R  static/req2prod/index.html -> site/main/index.html
D  infra/README-req2prod.md
A  infra/README-site.md
M  .github/workflows/deploy-to-prod.yml
M  .github/workflows/req2prod-pipeline.yml
```

```bash
git commit -m "Move the one-pager into site/ and sync the whole tree on deploy

The deploy copied one named file, so every new page needed a new workflow
step. Sync the site/ tree instead: no deploy step names a page, and adding a
page needs no workflow change.

The page moves to site/main/index.html, to be served at req2prod.nl/main.
/var/www/req2prod is deliberately left in place so the old URL keeps serving
until it is flipped to a 301.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Stand up req2prod.nl — DNS, server block, certificate

> **OPERATOR TASK — Marco performs every step. An agent must stop here, present
> these commands, and wait.** DNS and certbot are not automatable, and the reload
> is a production change.

**Files:**
- Create: `infra/nginx-req2prod.conf`

**Interfaces:**
- Consumes: `site/main/index.html` from Task 2, published to
  `/var/www/req2prod.nl/main/index.html` by `infra/sync-site.sh`.
- Produces: `https://req2prod.nl/main` serving the page over TLS.

- [ ] **Step 1 (agent): Create the pre-certbot server block**

Create `infra/nginx-req2prod.conf`. This is HTTP-only on purpose — certbot adds
the TLS directives itself when it runs, exactly as it did for the job-finder
domain.

```nginx
# The req2prod.nl product site. Static only: this domain has no Streamlit proxy,
# so `location /` is a plain static root and no page needs its own block.
#
# Served from /var/www, never from the repo checkout -- the web server must not
# have traverse permission into the app user's home directory.
#
# HTTP-only as committed. certbot --nginx adds the listen 443 / ssl_* lines and
# the port 80 redirect in place; re-copy the live file back here afterwards.
server {
    listen 80;
    server_name req2prod.nl www.req2prod.nl;

    root /var/www/req2prod.nl;

    # No homepage exists yet. /main is the URL that gets shared and must never
    # move, so it does not live at the root. Delete this block when a real
    # homepage lands at site/index.html; try_files will then serve it.
    location = / {
        return 301 /main;
    }

    location / {
        try_files $uri $uri/ =404;
    }
}
```

- [ ] **Step 2 (agent): Verify the config is syntactically valid before handing it over**

Run: `python3 -c "print(open('infra/nginx-req2prod.conf').read().count('{') == open('infra/nginx-req2prod.conf').read().count('}'))"`

Expected: `True`. (Real validation is `nginx -t` on the box in Step 6 — this only
catches an unbalanced brace before Marco spends a round trip.)

- [ ] **Step 3 (agent): Commit and hand off**

```bash
git add infra/nginx-req2prod.conf
git commit -m "Add the req2prod.nl nginx server block

Static root with try_files -- this domain has no Streamlit proxy, so one block
serves every page and no page needs its own config.

HTTP-only as committed; certbot adds TLS in place on the box.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

Stop here. Steps 4-9 are Marco's.

- [ ] **Step 4 (Marco): Change DNS at one.com**

| Type | Name | Value |
|---|---|---|
| `A` | `@` | `16.171.202.23` |
| `A` | `www` | `16.171.202.23` |

Change both from `46.30.211.38`. **A records only — do not add AAAA:** nginx
listens on IPv4 only, so an AAAA record would make IPv6 clients prefer IPv6 and
fail. Leave the null MX (`0 .`) alone. Lower TTL to 300s first if one.com allows.

- [ ] **Step 5 (Marco): Wait for DNS, then verify from the Mac**

```
dig +short req2prod.nl A
```

Expected: `16.171.202.23`. **Do not proceed until this returns the new IP** —
certbot proves ownership over HTTP on that IP and will fail otherwise.

Expect req2prod.nl to serve Streamlit in this window: DNS points at the box but
no server block claims that name yet, so nginx falls through to the default. That
is expected and resolves at Step 6.

- [ ] **Step 6 (Marco): Install the server block**

SSH in first (single paste — the key expires in ~2 minutes):

```
aws lightsail get-instance-access-details --instance-name job-finder --region eu-north-1 --protocol ssh --no-cli-pager > /tmp/ls-access.json && jq -r '.accessDetails.privateKey' /tmp/ls-access.json > /tmp/ls-key && jq -r '.accessDetails.certKey' /tmp/ls-access.json > /tmp/ls-key-cert.pub && chmod 600 /tmp/ls-key && ssh -i /tmp/ls-key ubuntu@16.171.202.23
```

Then on the box:

```
cd ~/crewai-starter && git pull --ff-only origin main
```

```
sudo cp infra/nginx-req2prod.conf /etc/nginx/sites-available/req2prod
```

```
sudo ln -sf /etc/nginx/sites-available/req2prod /etc/nginx/sites-enabled/req2prod
```

```
sudo nginx -t
```

Reload only if that passes:

```
sudo systemctl reload nginx
```

- [ ] **Step 7 (Marco): Publish the site content**

```
sudo ~/crewai-starter/infra/sync-site.sh ~/crewai-starter
```

Expected: `synced /home/ubuntu/crewai-starter/site -> /var/www/req2prod.nl`

- [ ] **Step 8 (Marco): Issue the certificate**

```
sudo certbot --nginx -d req2prod.nl -d www.req2prod.nl
```

certbot rewrites `/etc/nginx/sites-available/req2prod` in place, adding TLS and
the port-80 redirect, then reloads nginx itself.

- [ ] **Step 9 (Marco): Verify by content, from the Mac**

```
curl -fsS https://req2prod.nl/main | grep -o "<title>[^<]*</title>"
```

Expected: `<title>Req2Prod — Overview</title>`.

`<title>Streamlit</title>` means the server block is not active and the request
fell through to the job-finder default — do not proceed, and check
`sudo tail -5 /var/log/nginx/error.log`.

Also confirm the root redirect and that the app is undisturbed:

```
curl -sI https://req2prod.nl/ | grep -iE "^HTTP|^location"
```

Expected: `301` and `location: /main`.

```
curl -fsS https://yourmagicaljobfinder.online/ | grep -o "<title>[^<]*</title>"
```

Expected: `<title>Streamlit</title>` — the app is unaffected.

- [ ] **Step 10 (Marco): Copy the certbot-modified config back into the repo**

certbot edited the live file, so the repo copy is now stale. On the box:

```
sudo cp /etc/nginx/sites-enabled/req2prod ~/crewai-starter/infra/nginx-req2prod.conf
```

Then commit it from the Mac (or hand it back to the agent) so the repo matches
what is actually serving.

---

### Task 4: Flip the old URL to a 301 and retire the old machinery

**Do not start until Task 3 Step 9 passes.** Redirecting to a target that does not
exist sends investors to a 404.

**Files:**
- Modify: `infra/nginx-jobfinder.conf` (the `location = /req2prod` block)

**Interfaces:**
- Consumes: a verified-live `https://req2prod.nl/main` from Task 3.

- [ ] **Step 1: Write the failing verification**

This is the check that must flip from failing to passing. Run it now, before the
change:

```bash
curl -sI https://yourmagicaljobfinder.online/req2prod | grep -iE "^HTTP|^location"
```

Expected now (failing): `HTTP/1.1 200 OK` and no `location` header — it still
serves the file.

- [ ] **Step 2: Replace the block**

In `infra/nginx-jobfinder.conf`, find:

```nginx
    # Static Req2Prod investor one-pager. Exact-match so it takes priority over
    # the "location /" Streamlit proxy below without affecting any other path.
    #
    # Served from /var/www rather than the repo checkout, so that nginx needs no
    # traverse permission into the app user's home directory. Deploy copies the
    # file out of static/req2prod/ into /var/www/req2prod/ -- see
    # infra/README-req2prod.md.
    location = /req2prod {
        alias /var/www/req2prod/index.html;
        default_type text/html;
        add_header Cache-Control "no-store";
    }
```

Replace with:

```nginx
    # The one-pager moved to its own domain. Kept as a permanent redirect because
    # this URL was shared publicly before the move -- see infra/README-site.md.
    location = /req2prod {
        return 301 https://req2prod.nl/main;
    }
```

- [ ] **Step 3: Commit**

```bash
git add infra/nginx-jobfinder.conf
git commit -m "Redirect the old one-pager URL to req2prod.nl

The page now lives on its own domain. Serving it at both URLs would mean two
copies updated by two mechanisms, which is the drift this move exists to
prevent. 301 rather than removal because the URL was shared publicly.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4 (Marco): Deploy the nginx change**

Workflows do not manage nginx config. On the box:

```
cd ~/crewai-starter && git pull --ff-only origin main
```

```
sudo cp infra/nginx-jobfinder.conf /etc/nginx/sites-enabled/jobfinder
```

```
sudo nginx -t
```

```
sudo systemctl reload nginx
```

- [ ] **Step 5 (Marco): Remove the orphaned web root**

Nothing manages this directory any more; left alone it serves a frozen copy
forever. Only run this after Step 6 confirms the redirect works.

```
sudo rm -rf /var/www/req2prod
```

- [ ] **Step 6: Run the verification from Step 1 again**

```bash
curl -sI https://yourmagicaljobfinder.online/req2prod | grep -iE "^HTTP|^location"
```

Expected (passing): `HTTP/1.1 301 Moved Permanently` and
`location: https://req2prod.nl/main`.

Then confirm the redirect actually lands on the page:

```bash
curl -fsSL https://yourmagicaljobfinder.online/req2prod | grep -o "<title>[^<]*</title>"
```

Expected: `<title>Req2Prod — Overview</title>` (`-L` follows the redirect).

---

### Task 5: Prove the automation with a real second page

The whole point of this work is that page two costs nothing. This task tests that
claim end-to-end — if it needs any nginx or workflow change, the design failed.

**Files:**
- Create: `site/test-page/index.html` (temporary, deleted in Step 5)

- [ ] **Step 1: Add a page and nothing else**

```bash
mkdir -p site/test-page
cat > site/test-page/index.html <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Req2Prod — Publishing Check</title>
</head>
<body>
<h1>Publishing check</h1>
<p>If you can read this at req2prod.nl/test-page, publishing a page needed no
nginx change and no workflow change.</p>
</body>
</html>
HTML
```

- [ ] **Step 2: Confirm nothing else changed**

Run: `git status --short`

Expected: exactly one new file, `site/test-page/index.html`. If this task made you
touch nginx config or a workflow, **stop** — the design's core claim is broken and
the plan needs revisiting.

- [ ] **Step 3: Commit**

```bash
git add site/test-page/index.html
git commit -m "Add a temporary page to verify publishing needs no config change

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4 (Marco): Deploy and verify**

Merge, then trigger the deploy as usual. Then from the Mac:

```
curl -fsS https://req2prod.nl/test-page | grep -o "<title>[^<]*</title>"
```

Expected: `<title>Req2Prod — Publishing Check</title>`, with no nginx reload and
no SSH anywhere in the process.

- [ ] **Step 5: Delete the page and prove --delete unpublishes it**

```bash
git rm -r site/test-page
git commit -m "Remove the publishing check page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

After merge and deploy, from the Mac:

```
curl -sI https://req2prod.nl/test-page | grep -iE "^HTTP"
```

Expected: `HTTP/1.1 404 Not Found` — confirming `rsync --delete` mirrors removals
and the site cannot accumulate orphans.

---

## Done when

- `https://req2prod.nl/main` serves the one-pager over TLS, verified by content.
- `https://req2prod.nl/` 301s to `/main`.
- `https://yourmagicaljobfinder.online/req2prod` 301s to `https://req2prod.nl/main`.
- `https://yourmagicaljobfinder.online/` still serves Streamlit, unaffected.
- Adding and removing `site/test-page/` published and unpublished it with no
  nginx or workflow change (Task 5).
- `/var/www/req2prod` is gone from the box.
- No `static/req2prod` or `README-req2prod.md` references remain in the repo.
