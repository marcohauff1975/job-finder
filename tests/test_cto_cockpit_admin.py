"""
Unit tests for cto_cockpit_admin.py - the CTO Cockpit "live architecture"
page. Unlike most of this app's Streamlit UI, this feature's data source
is pure local filesystem reads (no network calls, no auth), which makes
it genuinely unit-testable: the drift guard, the data-building functions,
and the SVG renderer are all tested directly as pure functions, with one
streamlit.testing.v1.AppTest smoke test on top for the actual Streamlit
wiring - the first AppTest-based test in this repo.
"""

from streamlit.testing.v1 import AppTest

import cto_cockpit_admin as m


class TestUnclassifiedTopLevelPaths:
    def test_the_real_repo_has_nothing_unclassified(self):
        """The single highest-value test here: fires the moment anyone
        adds a new top-level file/dir without updating PRODUCT_PATHS/
        SHARED_INFRA_PATHS/EXCLUDED_NOISE, instead of it silently just
        not appearing anywhere in the live diagram."""
        assert m.unclassified_top_level_paths() == []

    def test_flags_a_genuinely_unknown_entry(self, tmp_path):
        (tmp_path / "some_new_top_level_thing.py").write_text("")

        assert m.unclassified_top_level_paths(tmp_path) == ["some_new_top_level_thing.py"]

    def test_does_not_flag_known_paths(self, tmp_path):
        (tmp_path / "auth.py").write_text("")
        (tmp_path / "infra").mkdir()
        (tmp_path / "data").mkdir()  # EXCLUDED_NOISE

        assert m.unclassified_top_level_paths(tmp_path) == []


class TestListOneLevelDeeper:
    def test_nonexistent_path_returns_empty(self, tmp_path):
        assert m._list_one_level_deeper(tmp_path, "does_not_exist.py") == []

    def test_file_returns_itself(self, tmp_path):
        (tmp_path / "auth.py").write_text("")

        assert m._list_one_level_deeper(tmp_path, "auth.py") == ["auth.py"]

    def test_directory_returns_immediate_children_only(self, tmp_path):
        pkg = tmp_path / "req2prod"
        pkg.mkdir()
        (pkg / "backend.py").write_text("")
        nested = pkg / "tools"
        nested.mkdir()
        (nested / "prod_ops.py").write_text("")  # grandchild - must NOT appear

        result = m._list_one_level_deeper(tmp_path, "req2prod")

        assert result == ["req2prod/backend.py", "req2prod/tools/"]

    def test_skips_pycache_noise_wherever_it_appears(self, tmp_path):
        pkg = tmp_path / "req2prod"
        pkg.mkdir()
        (pkg / "backend.py").write_text("")
        (pkg / "__pycache__").mkdir()

        result = m._list_one_level_deeper(tmp_path, "req2prod")

        assert result == ["req2prod/backend.py"]


class TestBuildProductTree:
    def _fake_repo(self, tmp_path, product_paths):
        for path, _product in product_paths.items():
            target = tmp_path / path
            if path.endswith(".py"):
                target.write_text("")
            else:
                target.mkdir()
                (target / "inner.py").write_text("")
        return product_paths

    def test_groups_paths_by_declared_product(self, tmp_path, monkeypatch):
        product_paths = {"auth.py": "Job Finder", "req2prod": "Req2Prod"}
        self._fake_repo(tmp_path, product_paths)
        monkeypatch.setattr(m, "PRODUCT_PATHS", product_paths)
        monkeypatch.setattr(m, "PRODUCTS", ["Job Finder", "Req2Prod"])

        tree = m.build_product_tree(tmp_path)

        assert tree["Job Finder"] == ["auth.py"]
        assert tree["Req2Prod"] == ["req2prod/inner.py"]

    def test_is_genuinely_live_no_code_change_needed(self, tmp_path, monkeypatch):
        """Proves the actual design claim: add a file to a product's
        declared directory and the very next build reflects it, with
        zero changes to this module."""
        product_paths = {"req2prod": "Req2Prod"}
        self._fake_repo(tmp_path, product_paths)
        monkeypatch.setattr(m, "PRODUCT_PATHS", product_paths)
        monkeypatch.setattr(m, "PRODUCTS", ["Req2Prod"])

        before = m.build_product_tree(tmp_path)
        assert "req2prod/new_module.py" not in before["Req2Prod"]

        (tmp_path / "req2prod" / "new_module.py").write_text("")
        after = m.build_product_tree(tmp_path)

        assert "req2prod/new_module.py" in after["Req2Prod"]


class TestBuildSharedInfraTree:
    def test_lists_declared_shared_paths(self, tmp_path, monkeypatch):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").write_text("")
        monkeypatch.setattr(m, "SHARED_INFRA_PATHS", ["tests"])

        assert m.build_shared_infra_tree(tmp_path) == ["tests/test_auth.py"]


class TestPathCountLabel:
    def test_singular(self):
        assert m._path_count_label(1) == "1 path"

    def test_plural(self):
        assert m._path_count_label(0) == "0 paths"
        assert m._path_count_label(2) == "2 paths"


class TestRenderArchitectureSvg:
    def test_wraps_in_a_valid_svg_element(self):
        svg = m.render_architecture_svg(products=["Job Finder", "Req2Prod"])

        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")

    def test_one_box_and_label_per_product(self):
        svg = m.render_architecture_svg(products=["Job Finder", "Req2Prod", "CTO Cockpit"])

        # background + one box per product + the foundation box
        assert svg.count("<rect") == 1 + 3 + 1
        assert "Job Finder" in svg
        assert "Req2Prod" in svg
        assert "CTO Cockpit" in svg
        assert "Infra as code (shared foundation)" in svg

    def test_cto_cockpit_box_is_dashed_not_yet_built(self):
        svg = m.render_architecture_svg(products=["CTO Cockpit"])

        assert "stroke-dasharray" in svg
        assert "not yet built" in svg

    def test_other_products_are_not_dashed(self):
        svg = m.render_architecture_svg(products=["Job Finder"])

        assert "stroke-dasharray" not in svg

    def test_path_counts_render_in_the_label(self):
        svg = m.render_architecture_svg(products=["Job Finder"], path_counts={"Job Finder": 7})

        assert "Job Finder (7 paths)" in svg

    def test_deterministic(self):
        assert m.render_architecture_svg() == m.render_architecture_svg()

    def test_width_scales_with_product_count(self):
        narrow = m.render_architecture_svg(products=["Job Finder"])
        wide = m.render_architecture_svg(products=["Job Finder", "Req2Prod", "CTO Cockpit"])

        def _width(svg: str) -> int:
            return int(svg.split('viewBox="0 0 ')[1].split(" ")[0])

        assert _width(wide) > _width(narrow)


_SMOKE_SCRIPT = """
import cto_cockpit_admin as m

m.render_cto_cockpit_tab()
"""


class TestRenderCtoCockpitTabSmoke:
    """AppTest.from_function only extracts the target function's own
    source lines (via inspect.getsourcelines()) - it never carries the
    enclosing module's imports, so it can't see this module's
    `import streamlit as st`. AppTest.from_string runs a small
    self-contained script instead, which can import and call the real
    function normally."""

    def test_renders_with_no_exceptions(self):
        at = AppTest.from_string(_SMOKE_SCRIPT)

        at.run(timeout=30)

        assert not at.exception

    def test_shows_the_architecture_svg(self):
        at = AppTest.from_string(_SMOKE_SCRIPT)

        at.run(timeout=30)

        markdown_html = "\n".join(md.value for md in at.markdown)
        assert "<svg" in markdown_html

    def test_shows_one_expander_per_product_plus_shared(self):
        at = AppTest.from_string(_SMOKE_SCRIPT)

        at.run(timeout=30)

        assert len(at.expander) == len(m.PRODUCTS) + 1

    def test_shows_the_aws_setup_placeholder(self):
        at = AppTest.from_string(_SMOKE_SCRIPT)

        at.run(timeout=30)

        assert any("Coming soon" in info.value for info in at.info)
