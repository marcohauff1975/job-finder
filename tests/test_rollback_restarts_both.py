"""
A rollback must restart every service running out of the reverted checkout.

`git reset --hard <previous_commit>` reverts the whole directory - both apps'
code - but each process keeps running from memory until restarted. Restarting
only jobfinder leaves the admin console serving the exact code the rollback
just decided was bad, until some unrelated future deploy happens to touch an
admin file. Found by code_reviewer on PR #91, which is the PR that added the
second service.

No SSH: _ssh_run is stubbed and the commands it would have run are recorded.
"""

import req2prod.tools.prod_ops as prod_ops


def _capture(monkeypatch):
    ran = []

    def fake_ssh_run(key_path, remote_user, instance_ip, command):
        ran.append(command)
        return 0, "ok", ""

    monkeypatch.setattr(prod_ops, "_ssh_run", fake_ssh_run)
    monkeypatch.setattr(prod_ops, "_curl_status", lambda url: "200")
    monkeypatch.setattr(prod_ops, "_check_page_renders_cleanly", lambda url: {})
    return ran


def _rollback(monkeypatch, **kwargs):
    ran = _capture(monkeypatch)
    prod_ops.ProdRollbackTool()._run(
        instance_ip="1.2.3.4",
        key_path="/tmp/k",
        remote_user="ubuntu",
        remote_app_dir="/home/ubuntu/app",
        service_name="jobfinder.service",
        previous_commit="abc123",
        service_url="https://example.test",
        **kwargs,
    )
    return ran


class TestBothServicesComeBack:
    def test_it_restarts_the_admin_console_too(self, monkeypatch):
        ran = _rollback(monkeypatch, admin_service_name="req2prod.service")

        assert "sudo systemctl restart jobfinder.service" in ran
        assert "sudo systemctl restart req2prod.service" in ran

    def test_it_still_resets_the_checkout_first(self, monkeypatch):
        """Order matters: restart before the reset and the process reloads the
        bad code."""
        ran = _rollback(monkeypatch, admin_service_name="req2prod.service")

        reset = next(i for i, c in enumerate(ran) if "git reset --hard abc123" in c)
        restarts = [i for i, c in enumerate(ran) if "systemctl restart" in c]
        assert all(i > reset for i in restarts)

    def test_one_service_boxes_still_work(self, monkeypatch):
        """admin_service_name is optional - a box with only jobfinder.service
        must roll back exactly as before."""
        ran = _rollback(monkeypatch)

        assert "sudo systemctl restart jobfinder.service" in ran
        assert not any("req2prod.service" in c for c in ran)

    def test_an_empty_admin_name_restarts_nothing_extra(self, monkeypatch):
        ran = _rollback(monkeypatch, admin_service_name="")

        assert len([c for c in ran if "systemctl restart" in c]) == 1
