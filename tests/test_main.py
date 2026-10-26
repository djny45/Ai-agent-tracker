from app.main import github_headers, parse_datetime, settings, validate_monitor_url
from fastapi import HTTPException


def test_parse_datetime_normalizes_z_timezone():
    value = parse_datetime("2026-10-26T10:00:00Z")
    assert value is not None
    assert value.utcoffset().total_seconds() == 0
    assert value.tzinfo is not None


def test_parse_datetime_returns_none_for_missing_or_invalid_values():
    assert parse_datetime(None) is None
    assert parse_datetime("not-a-date") is None


def test_github_headers_include_api_version_and_user_agent():
    headers = github_headers()
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert headers["User-Agent"] == "Universal-Agent-Tracker/0.2"
    assert "Authorization" not in headers


def test_monitor_url_requires_https_and_exact_allowlisted_host():
    original = settings.monitor_allowed_hosts
    settings.monitor_allowed_hosts = "status.example.com"
    try:
        assert validate_monitor_url("https://status.example.com/health") == "status.example.com"
        for url in (
            "http://status.example.com/health",
            "https://other.example.com/health",
            "https://127.0.0.1/health",
            "https://user:pass@status.example.com/health",
            "https://status.example.com/health#fragment",
        ):
            try:
                validate_monitor_url(url)
            except HTTPException:
                pass
            else:
                raise AssertionError(f"Expected URL to be rejected: {url}")
    finally:
        settings.monitor_allowed_hosts = original


def test_monitor_url_fails_closed_when_allowlist_is_empty():
    original = settings.monitor_allowed_hosts
    settings.monitor_allowed_hosts = ""
    try:
        try:
            validate_monitor_url("https://status.example.com/health")
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("Expected empty allowlist to reject endpoint")
    finally:
        settings.monitor_allowed_hosts = original
