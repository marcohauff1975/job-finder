# Req2Prod one-pager — `/req2prod`

The Req2Prod investor one-pager is served as a static page at
<https://yourmagicaljobfinder.online/req2prod>.

It is a single self-contained HTML file (inline CSS + SVG, no external assets),
tracked at `static/req2prod/index.html`.

## Why the extra copy step

nginx serves the page from `/var/www/req2prod/index.html`, **not** directly from
the repo checkout, even though the file is tracked here.

Serving it straight from the checkout would mean granting the web server traverse
permission into the app user's home directory, which is where the application's
runtime data and configuration live. Keeping the web server out of that directory
entirely is the safer default — a static page is not worth widening what nginx can
reach — so the file is copied out to the web root instead.

The repo is only the *delivery* mechanism (it rides the existing `git pull` deploy
channel to the box); `/var/www` is the *serve* location.

## Deploying a change to the one-pager

Deploys on this box are manual. From `/home/ubuntu/crewai-starter` on the server:

```
git pull --ff-only origin main
```

```
sudo mkdir -p /var/www/req2prod
```

```
sudo cp static/req2prod/index.html /var/www/req2prod/index.html
```

Editing the page itself needs nothing further — nginx reads the file per request
and `Cache-Control: no-store` prevents caching.

Only if `infra/nginx-jobfinder.conf` itself changed:

```
sudo cp infra/nginx-jobfinder.conf /etc/nginx/sites-enabled/jobfinder
```

```
sudo nginx -t
```

```
sudo systemctl reload nginx
```

`reload` is graceful — it never restarts `jobfinder.service`, so live Streamlit
sessions are not dropped.

## Verifying

```
curl -sI https://yourmagicaljobfinder.online/req2prod | head -3
```

- `200` — live.
- `404` — the `cp` into `/var/www/req2prod/` was skipped.
- `403` — permissions on `/var/www/req2prod/`; check with
  `namei -l /var/www/req2prod/index.html`.
