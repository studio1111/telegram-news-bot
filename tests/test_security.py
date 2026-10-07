import pytest

from app.security import validate_public_url


def test_rejects_non_http_urls():
    with pytest.raises(ValueError):
        validate_public_url("file:///etc/passwd")


def test_rejects_non_allowlisted_hosts():
    with pytest.raises(ValueError):
        validate_public_url("https://example.invalid/story")
