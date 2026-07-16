# req2prod.nl — the product site

> **Live.** All three operator steps have landed: `req2prod.nl` resolves to this
> box, the nginx server block exists, and certbot has issued a certificate.
> <https://req2prod.nl> serves this tree over TLS, and `site/index.html` is
> safe to edit -- merge to main, deploy, done.
>
> This note previously said the opposite, and warned against editing
> `site/index.html` because the live page was still served from a frozen copy at
> `/var/www/req2prod/`. That was true when written and stopped being true once
> the DNS moved. Nothing tells a doc it has gone stale, so: if you change how
> this is served, change this paragraph in the same commit.

Everything under `site/` is published to <https://req2prod.nl>. The tree is the
site: the path in this repo is the path on the web.

```
site/index.html        ->  https://req2prod.nl/
site/details.html      ->  https://req2prod.nl/details.html
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

nginx will serve `/var/www/req2prod.nl` (`root` + `try_files`), not this
checkout — the web server has no traverse permission into the app user's home
directory, and it should not have any. `infra/sync-site.sh` already copies the
tree out to that web root on every deploy; both deploy workflows call it right
after pulling. What's still missing is the nginx server block itself, plus DNS
and a certificate — see the note at the top of this file.

`site/index.html` is the homepage, served at `/`. There is no `/main` and no
root redirect: an earlier draft parked the one-pager at `/main` because no
homepage existed, but it is the homepage now, so `try_files` serves it at the
root directly. The pages' own nav links to `/` and `/details.html`, so those are
the URLs that must keep working — check the nav in both pages before moving or
renaming anything under `site/`.

## Verifying

This only works once the site is live (see the note at the top). Check
content, never just the status code — a `200` from this box can mean the
Streamlit app answered instead of the page:

```
curl -fsS https://req2prod.nl/ | grep -q 'Req2Prod — Overview' && echo LIVE
curl -fsS https://req2prod.nl/details.html | grep -q 'Requirement to production' && echo LIVE
```
