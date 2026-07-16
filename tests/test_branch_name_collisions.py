"""
A branch name already on the remote must not sink the build.

The demo requests are meant to be run repeatedly and always ask for the same
branch, so a collision is a certainty. The workspace is a fresh clone of main,
so its branch and the old one have diverged - the push is rejected as a
non-fast-forward, the PR never opens, and pr_url comes back empty. Observed
2026-07-16: feature/remove-req2prod-demo-logo was still on the remote from a
demo merged two hours earlier.

No network: _remote_branch_exists is stubbed.
"""

import req2prod.tools.feature_build_ops as ops


class TestPickingAFreeName:
    def test_a_free_name_is_used_as_is(self, monkeypatch):
        monkeypatch.setattr(ops, "_remote_branch_exists", lambda b, t: False)

        assert ops._unused_branch_name("feature/x", "tok") == "feature/x"

    def test_a_taken_name_gets_a_suffix(self, monkeypatch):
        taken = {"feature/remove-req2prod-demo-logo"}
        monkeypatch.setattr(ops, "_remote_branch_exists", lambda b, t: b in taken)

        assert ops._unused_branch_name("feature/remove-req2prod-demo-logo", "tok") == (
            "feature/remove-req2prod-demo-logo-2"
        )

    def test_it_counts_up_past_several_runs(self, monkeypatch):
        """Readable, and a fourth run is obviously the fourth."""
        taken = {"feature/x", "feature/x-2", "feature/x-3"}
        monkeypatch.setattr(ops, "_remote_branch_exists", lambda b, t: b in taken)

        assert ops._unused_branch_name("feature/x", "tok") == "feature/x-4"

    def test_it_gives_up_rather_than_looping(self, monkeypatch):
        """If everything is taken, fail the push with a message naming a real
        branch instead of spinning."""
        monkeypatch.setattr(ops, "_remote_branch_exists", lambda b, t: True)

        assert ops._unused_branch_name("feature/x", "tok") == "feature/x"

    def test_it_never_force_pushes_over_the_existing_branch(self):
        """The branch it collides with may be someone's open PR. Overwriting
        it to save a name is not a trade worth making."""
        import inspect

        source = inspect.getsource(ops)

        assert "--force" not in source
        assert "push --delete" not in source


class TestTheToolActuallyUsesIt:
    """The unit tests above pass even if _run never calls _unused_branch_name -
    a fix nothing exercises is a fix that rots. These drive the real _run and
    assert on the branch it actually pushes."""

    def _drive(self, monkeypatch, tmp_path, taken):
        monkeypatch.setenv("GITHUB_PR_PUSH_TOKEN", "tok")
        monkeypatch.setattr(ops, "_remote_branch_exists", lambda b, t: b in taken)
        # workspace_dir() raises outside a live build_workspace() block - the
        # path-escape guard that exists because these tools once wiped the live
        # app's streamlit_app.py. Satisfy it rather than route around it.
        monkeypatch.setattr(ops, "workspace_dir", lambda: tmp_path)

        ran = []

        def fake_run_in_workspace(args, **kwargs):
            ran.append(args)
            return 0, "", ""

        monkeypatch.setattr(ops, "run_in_workspace", fake_run_in_workspace)

        class FakeResp:
            status_code = 201
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"html_url": "https://github.com/marcohauff1975/job-finder/pull/1"}

        monkeypatch.setattr(ops.requests, "post", lambda *a, **kw: FakeResp())
        ops.CreateFeatureBranchAndOpenPRTool()._run(
            branch_name="feature/remove-req2prod-demo-logo",
            file_paths=["streamlit_app.py"],
            commit_message="remove the logo",
            pr_title="Remove the logo",
            pr_body="body",
        )
        return ran

    def test_it_pushes_the_suffixed_branch_when_the_name_is_taken(self, monkeypatch, tmp_path):
        ran = self._drive(monkeypatch, tmp_path, taken={"feature/remove-req2prod-demo-logo"})

        pushes = [a for a in ran if a[:2] == ["git", "push"]]
        assert pushes, "no push happened"
        assert any("feature/remove-req2prod-demo-logo-2" in " ".join(a) for a in pushes)

    def test_it_pushes_the_plain_branch_when_the_name_is_free(self, monkeypatch, tmp_path):
        ran = self._drive(monkeypatch, tmp_path, taken=set())

        pushes = [a for a in ran if a[:2] == ["git", "push"]]
        assert any(
            "feature/remove-req2prod-demo-logo:feature/remove-req2prod-demo-logo" in " ".join(a)
            for a in pushes
        )
