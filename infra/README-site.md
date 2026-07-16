# req2prod.nl — the product site

> **Not live yet.** Everything below describes the intended end state, not
> what is reachable today. `req2prod.nl` does not resolve to this box yet:
> DNS has not been pointed at it, the nginx server block for it does not
> exist, and no TLS certificate has been issued. The site goes live only once
> all three of those land as separate operator steps.
>
> **Until then, do not edit `site/index.html`.** The live page at
> `https://yourmagicaljobfinder.online/req2prod` is still served from the old
> web root (`/var/www/req2prod/`), which is now a frozen, unmanaged copy --
> nothing updates it any more. The deploy already syncs `site/` to the new web
> root (`/var/www/req2prod.nl`), but nginx isn't serving that root yet, so an
> edit to `site/index.html` only changes the unserved copy while the old,
> live copy keeps answering requests unchanged -- the published page would go
> stale silently, with no error to signal it. If the page must be edited
> before the redirect lands, update `/var/www/req2prod/index.html` on the box
> by hand too, so the live URL doesn't fall out of sync.

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
