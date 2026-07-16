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
