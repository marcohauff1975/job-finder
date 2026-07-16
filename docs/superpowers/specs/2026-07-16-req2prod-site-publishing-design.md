# Req2Prod site publishing — design

**Date:** 2026-07-16
**Status:** Approved, not yet implemented

## Problem

Publishing the Req2Prod one-pager took five manual steps, two wrong turns, and a
403 — to put *one* page on the internet. Everything built so far is hardcoded to
that single page:

- nginx has an **exact-match block per page** (`location = /req2prod`), aliasing
  one named file.
- each deploy workflow has a **hardcoded `cp`** naming `req2prod/index.html`.
- the nginx config sync is a **manual SSH**.

Publishing a second page today means editing the nginx config, adding a `cp` step
to two workflows, and hand-syncing nginx on the box. Four touchpoints per page,
each one a chance to get it wrong — as we did, twice.

Marco wants several more pages explaining the product, with detailed flows and
probably video. The current shape does not survive that.

## Goals

- Publishing a page is: **add the file, merge, deploy.** No nginx edit, no
  workflow edit, no SSH.
- Product pages live on **req2prod.nl**, the product's own domain.
- Support multi-file pages (HTML + CSS + images), not just self-contained files.
- One canonical URL per page. No content served at two addresses.

## Non-goals

- **A static site generator** (Hugo/Eleventy). At ~5 hand-authored pages, a
  toolchain costs more than the copy-pasted nav it saves. This design does not
  block adding one later.
- **S3 + CloudFront.** The right answer if this becomes a real marketing site
  under load, but it abandons the working certbot/rsync path for infrastructure
  we do not need yet.
- **Automating nginx config changes.** Deliberate — see Decisions.

## Architecture

### Content layout

A new top-level `site/` directory in the repo *is* the site. Path in the repo
equals path on the web:

```
site/
  main/index.html        ->  req2prod.nl/main
  flows/index.html       ->  req2prod.nl/flows        (later)
  flows/diagram.svg      ->  req2prod.nl/flows/diagram.svg
```

`static/req2prod/index.html` moves to `site/main/index.html`. Assets live beside
the page that uses them and are referenced relatively, so they resolve without
configuration.

### Serving

One nginx server block for `req2prod.nl` and `www.req2prod.nl`:

- `root /var/www/req2prod.nl`
- `try_files $uri $uri/ =404`
- TLS via certbot, same flow as the existing domain

This domain has no Streamlit proxy on `location /`, so `location /` is a plain
static root. That is the crux of the design: the per-page exact-match blocks exist
*only* to dodge the proxy on the job-finder domain. On its own domain there is
nothing to dodge, and one block serves every page that will ever exist here.

`/` temporarily 301s to `/main` until a real homepage exists, via an explicit
exact-match block — `try_files` alone would 404 the root, since there is no
`index.html` there:

```
location = / {
    return 301 /main;
}
```

`/main` is the URL shared with investors and must never move; if the one-pager sat
at the root, the day a homepage arrives it would displace it and break every
shared link. When a real homepage lands at `site/index.html`, this block is
deleted and `try_files` serves it — `/main` is unaffected.

### Deploy

Both workflows already `git pull` on the server, so `site/` arrives on the box for
free. The sync is local to the server and replaces the hardcoded `cp`:

```
sudo rsync -a --delete $REMOTE_APP_DIR/site/ /var/www/req2prod.nl/
```

`--delete` makes it a true mirror: deleting a page from the repo unpublishes it,
rather than leaving an orphan live forever. No step names a page, so new pages
need no workflow edit.

**Guard:** the step must verify `site/` exists and is non-empty *before* running,
and fail loudly otherwise. `--delete` against an empty source would wipe the web
root.

### Retiring the old machinery

The one-pager currently ships through a parallel path that this design replaces.
All of it must go in the same change, or deploys break:

- **Delete** the `Sync the req2prod one-pager to /var/www` step from both
  workflows. It copies `static/req2prod/index.html`, which will no longer exist —
  every deploy would fail on it.
- **Delete** `static/req2prod/` (the file moves to `site/main/index.html`).
- **Remove** `/var/www/req2prod/` from the box by hand, once. Nothing else manages
  that directory, so the orphaned page would otherwise keep serving indefinitely
  at the old alias.
- **Replace** the `location = /req2prod` file-serving block with the 301 (see
  Redirect). Retire `infra/README-req2prod.md`, whose manual `cp` instructions
  this design makes obsolete.

The old and new paths must not coexist: two copies of the page on disk, served at
two URLs, updated by two mechanisms is exactly the drift this design exists to
prevent.

### Redirect

`yourmagicaljobfinder.online/req2prod` becomes:

```
location = /req2prod {
    return 301 https://req2prod.nl/main;
}
```

Single canonical URL. The job-finder domain is the one we do not want shared.

## Verification

**Every check greps page content. No check may assert only a status code.**

```
curl -fsS https://req2prod.nl/main | grep -q 'Req2Prod — Overview'
```

This is not a stylistic preference; it is the lesson of 2026-07-16. During the
one-pager rollout, `https://yourmagicaljobfinder.online/req2prod` returned
`HTTP/1.1 200 OK` while serving **Streamlit's app shell** — the nginx block was
not active, so `location /` caught the path and the app answered cheerfully with
a 200. A status-code health check would have reported success while the page was
not live at all.

Streamlit's catch-all returns 200 for *any* path. If req2prod.nl's server block
is ever missing or misnamed, requests fall through to the default server — the
job-finder block — and Streamlit answers 200 again under the req2prod.nl name.
Status codes tell you something answered; only content tells you the right thing
answered.

The deploy's existing health check probes `:8501` only, which is why nothing
caught the stale-page bug found in review on #64. The rsync step gets its own
content check, and a non-200 or missing string fails the deploy.

## Cutover

Order matters — the redirect cannot be tested before its target exists.

1. **DNS at one.com** — point `req2prod.nl` and `www` at `16.171.202.23`
   (verified static: Lightsail static IP `job-finder-ip`). A records only; do
   **not** add AAAA (nginx listens on IPv4 only — `listen 443 ssl`, no `[::]`),
   or IPv6-capable clients would prefer IPv6 and fail. Leave the null MX (`0 .`)
   alone. Lower TTL to 300s beforehand if one.com allows it.
2. **nginx server block + certbot.** DNS must resolve to the box first — certbot
   proves ownership over HTTP on that IP and will fail otherwise.
3. **Content + rsync step** merged and deployed. Verify `/main` **by content**.
4. **Only then** flip `/req2prod` to the 301.

Doing 4 before 3 redirects investors to a 404.

**Expected window:** between steps 1 and 2, req2prod.nl resolves to the box with
no server block for it, so nginx falls through to the default (job-finder) block
and serves Streamlit under the req2prod.nl name. Cosmetic, expected, resolves at
step 2.

## Rollback

Every step is independently reversible; nothing here is one-way.

| Step | Rollback |
|---|---|
| DNS | Revert A records to `46.30.211.38` (one.com) |
| Server block | Remove the block, `nginx -t`, reload |
| Content | `git revert`, redeploy — rsync mirrors the revert |
| Redirect | Restore the file-serving block |

A failed `nginx -t` changes nothing: the old config keeps serving. Always test
before reload.

## Decisions

**Video is never committed to the repo.** Embed from YouTube/Vimeo via `<iframe>`.
The repo is **public** and git history is **permanent** — a committed video is
downloaded by every clone forever and cannot be removed without rewriting history.
Git is the wrong store for binaries. Self-hosting would also put streaming
bandwidth on a $12/mo box that is simultaneously running the Streamlit app. If
video must ever be private or self-hosted, it goes to S3/CloudFront, not git.

**nginx config stays manual, not workflow-managed.** A bad config would take down
the job-finder app too, since both server blocks live in the same nginx. This
design makes config edits *rare* — new pages never touch nginx — so automating a
rare, high-blast-radius step is a bad trade.

**DNS and TLS stay manual.** One-time, not sensibly automatable.

**Deploys stay manual and operator-triggered**, per the standing rule on this box.
This design changes *what* a deploy does, not *who* triggers it.

## Risks

- **Single box.** The site shares a $12/mo Lightsail instance with the Streamlit
  app; if the box dies, both die. Acceptable for a product site, not for a launch
  driving real traffic. S3/CloudFront is the escape hatch.
- **`--delete` is sharp.** Mirroring means a bad path or empty source could wipe
  the web root. Hence the guard.
- **The repo is public.** Anything added under `site/` is world-readable and
  permanent in history. That is fine for marketing pages; it must never hold
  anything confidential.
- **Crawlers find URLs immediately.** The nginx error log already shows external
  IPs requesting `/req2prod`. A URL is public the moment it exists.

## Out of scope (tracked separately)

- A production file-permissions hardening item, tracked outside this repo. It is
  deliberately not described here: this repo is public, and the details of a
  live system's weaknesses do not belong in a world-readable document. It is
  unrelated to this design and does not block it.
- The reviewer bot's PAT lacks `workflow` scope, so its fix agent cannot repair
  anything under `.github/workflows/` — it surfaces as a confusing "review failed"
  rather than "bot could not push".
