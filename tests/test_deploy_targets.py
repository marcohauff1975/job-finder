"""
Unit tests for req2prod/deploy_targets.py - which services a deploy restarts.

This is the half of the split that actually fixes the problem. Two processes
don't help if the deploy bounces both anyway: shipping a Job Finder change
would still kill the SDLC view someone is watching that deploy through.

Pure functions over path strings - no filesystem, no network, no git.
"""

from req2prod.deploy_targets import JOBFINDER, REQ2PROD, services_to_restart


class TestOneServiceAtATime:
    def test_a_job_finder_change_leaves_the_console_alone(self):
        """The whole point: watch this deploy in the SDLC view and it survives."""
        assert services_to_restart(["job_search.py"]) == {JOBFINDER}

    def test_a_console_change_leaves_the_public_app_alone(self):
        assert services_to_restart(["req2prod/admin_ui.py"]) == {REQ2PROD}

    def test_the_console_entry_point_is_the_console(self):
        assert services_to_restart(["req2prod_app.py"]) == {REQ2PROD}

    def test_the_public_entry_point_is_the_public_app(self):
        assert services_to_restart(["streamlit_app.py"]) == {JOBFINDER}

    def test_jobfinder_admin_is_the_console_despite_its_name(self):
        """Job Finder's product, but it renders inside the console. A
        product-based rule would restart the public app and leave the console
        stale - which is why PRODUCT_PATHS isn't reused here."""
        assert services_to_restart(["jobfinder_admin.py"]) == {REQ2PROD}

    def test_the_cto_cockpit_is_the_console_too(self):
        assert services_to_restart(["cto_cockpit_admin.py"]) == {REQ2PROD}
        assert services_to_restart(["cto_cockpit_connectivity.py"]) == {REQ2PROD}


class TestSharedCodeRestartsBoth:
    def test_auth_is_imported_by_both(self):
        assert services_to_restart(["auth.py"]) == {JOBFINDER, REQ2PROD}

    def test_reporting_is_imported_by_both(self):
        """It owns UNLIMITED_USER, which both processes read."""
        assert services_to_restart(["reporting.py"]) == {JOBFINDER, REQ2PROD}

    def test_requirements_restarts_both(self):
        assert services_to_restart(["requirements.txt"]) == {JOBFINDER, REQ2PROD}

    def test_a_diff_spanning_both_restarts_both(self):
        assert services_to_restart(["job_search.py", "req2prod/admin_ui.py"]) == {
            JOBFINDER,
            REQ2PROD,
        }


class TestUnknownGetsTheSafeAnswer:
    def test_an_unclassified_python_file_restarts_both(self):
        """Nobody has said which process imports this, so the rule doesn't
        guess - it does what the deploy did before any of this existed."""
        assert services_to_restart(["some_new_module.py"]) == {JOBFINDER, REQ2PROD}

    def test_that_is_never_worse_than_the_old_behaviour(self):
        """The old rule restarted on any .py. An unclassified .py still does."""
        assert JOBFINDER in services_to_restart(["whatever.py"])


class TestNonCodeRestartsNothing:
    """Also today's behaviour - the old rule keyed on `\\.py$`, so a page or a
    doc never triggered a restart. Preserved, not invented."""

    def test_the_published_site_needs_no_restart(self):
        assert services_to_restart(["site/index.html"]) == set()

    def test_docs_need_no_restart(self):
        assert services_to_restart(["docs/superpowers/specs/whatever.md"]) == set()

    def test_the_workflow_itself_needs_no_restart(self):
        assert services_to_restart([".github/workflows/req2prod-pipeline.yml"]) == set()

    def test_infra_needs_no_restart(self):
        """nginx and systemd files are applied by hand, never by the deploy."""
        assert services_to_restart(["infra/req2prod.service"]) == set()

    def test_an_empty_diff_restarts_nothing(self):
        assert services_to_restart([]) == set()

    def test_blank_lines_from_git_are_ignored(self):
        assert services_to_restart(["", "  ", "\n"]) == set()


class TestTheCli:
    """The deploy pipes `git diff --name-only` into this."""

    def test_it_prints_service_names_sorted(self, capsys, monkeypatch):
        import io
        import req2prod.deploy_targets as m

        monkeypatch.setattr(m.sys, "stdin", io.StringIO("auth.py\n"))
        m.main()

        assert capsys.readouterr().out.strip() == "jobfinder req2prod"

    def test_it_prints_nothing_when_nothing_needs_restarting(self, capsys, monkeypatch):
        import io
        import req2prod.deploy_targets as m

        monkeypatch.setattr(m.sys, "stdin", io.StringIO("site/index.html\n"))
        m.main()

        assert capsys.readouterr().out.strip() == ""


class TestNonCodeUnderAClassifiedPrefix:
    """The prefixes are matched only after _is_live_code, or they swallow
    things that aren't code: req2prod/lessons/*.md starts with "req2prod/",
    config/agents.yaml starts with "config/". Matching on prefix alone
    restarted a service - dropping every live session on it - for editing a
    note. Found by code_reviewer on PR #91; every non-code case tested before
    (site/, docs/, .github/, infra/) happened to fall outside both prefixes,
    so nothing caught it."""

    def test_a_lessons_note_restarts_nothing(self):
        assert services_to_restart(["req2prod/lessons/code_reviewer.md"]) == set()

    def test_a_plan_document_under_req2prod_restarts_nothing(self):
        assert services_to_restart(["req2prod/AGENT_INTELLIGENCE_PLAN.md"]) == set()

    def test_job_finder_crew_config_restarts_nothing(self):
        """config/agents.yaml matches the 'config/' prefix but isn't code.
        The old rule keyed on `\\.py$` and never restarted for it either."""
        assert services_to_restart(["config/agents.yaml"]) == set()

    def test_an_asset_restarts_nothing(self):
        assert services_to_restart(["assets/format_previews/one.png"]) == set()

    def test_python_under_those_prefixes_still_restarts_its_own_service(self):
        """The gate must not break the thing it guards."""
        assert services_to_restart(["req2prod/admin_ui.py"]) == {REQ2PROD}
        assert services_to_restart(["config/loader.py"]) == {JOBFINDER}
