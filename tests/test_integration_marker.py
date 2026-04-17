"""Verify that tests marked `integration` are skipped unless IB_RUN_INTEGRATION=1."""

import textwrap


def test_integration_marker_skipped_by_default(pytester, monkeypatch):
    monkeypatch.delenv("IB_RUN_INTEGRATION", raising=False)
    pytester.makeconftest(_conftest_source())
    pytester.makepyfile(
        test_sample=textwrap.dedent(
            """
            import pytest

            def test_plain():
                assert True

            @pytest.mark.integration
            def test_live():
                raise AssertionError("should have been skipped")
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, skipped=1)


def test_integration_marker_runs_when_env_set(pytester, monkeypatch):
    monkeypatch.setenv("IB_RUN_INTEGRATION", "1")
    pytester.makeconftest(_conftest_source())
    pytester.makepyfile(
        test_sample=textwrap.dedent(
            """
            import pytest

            @pytest.mark.integration
            def test_live():
                assert True
            """
        )
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)


def _conftest_source() -> str:
    return textwrap.dedent(
        """
        import os
        import pytest

        def pytest_configure(config):
            config.addinivalue_line(
                "markers",
                "integration: Tests requiring a running IB Gateway/TWS instance.",
            )

        def pytest_collection_modifyitems(config, items):
            if os.environ.get("IB_RUN_INTEGRATION") == "1":
                return
            skip = pytest.mark.skip(reason="requires IB_RUN_INTEGRATION=1")
            for item in items:
                if "integration" in item.keywords:
                    item.add_marker(skip)
        """
    )
