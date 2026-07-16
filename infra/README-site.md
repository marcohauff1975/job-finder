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
