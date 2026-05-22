"""Tests for ``get_allowed_categories`` — registry fetch with fallback.

The category whitelist used to live as a hardcoded list inside the
``validate_category`` field validator. It now comes from the backend's
``/api/categories`` endpoint, falling back to ``FALLBACK_CATEGORIES`` on
any error so offline / pre-commit / CI flows keep working.

These tests cover:
- success path returns the registry's ids and caches them
- bare-array response shape is accepted (forward-compat)
- network errors fall back silently to the hardcoded list
- non-200 responses fall back
- malformed JSON falls back
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests

from toolbase import validation


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the per-invocation cache between tests."""
    validation._categories_cache = None
    yield
    validation._categories_cache = None


def _fake_response(payload, status: int = 200):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


def test_success_returns_ids_from_registry():
    payload = {
        "categories": [
            {"id": "astro", "name": "Astrophysics"},
            {"id": "newdomain", "name": "Brand New Domain"},
        ]
    }
    with mock.patch.object(requests, "get", return_value=_fake_response(payload)):
        ids = validation.get_allowed_categories()
    assert ids == ["astro", "newdomain"]


def test_bare_array_response_accepted():
    payload = [
        {"id": "astro", "name": "Astrophysics"},
        {"id": "hep", "name": "HEP"},
    ]
    with mock.patch.object(requests, "get", return_value=_fake_response(payload)):
        ids = validation.get_allowed_categories()
    assert ids == ["astro", "hep"]


def test_string_array_response_accepted():
    """Forward-compat: backend may simplify to a bare list of ids."""
    payload = ["astro", "hep"]
    with mock.patch.object(requests, "get", return_value=_fake_response(payload)):
        ids = validation.get_allowed_categories()
    assert ids == ["astro", "hep"]


def test_network_error_falls_back():
    with mock.patch.object(
        requests, "get", side_effect=requests.exceptions.ConnectionError("offline")
    ):
        ids = validation.get_allowed_categories()
    assert ids == validation.FALLBACK_CATEGORIES


def test_non_200_falls_back():
    with mock.patch.object(
        requests, "get", return_value=_fake_response({}, status=503)
    ):
        ids = validation.get_allowed_categories()
    assert ids == validation.FALLBACK_CATEGORIES


def test_malformed_json_falls_back():
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    with mock.patch.object(requests, "get", return_value=resp):
        ids = validation.get_allowed_categories()
    assert ids == validation.FALLBACK_CATEGORIES


def test_empty_category_list_falls_back():
    with mock.patch.object(
        requests, "get", return_value=_fake_response({"categories": []})
    ):
        ids = validation.get_allowed_categories()
    assert ids == validation.FALLBACK_CATEGORIES


def test_result_is_cached():
    payload = {"categories": [{"id": "astro", "name": "Astrophysics"}]}
    with mock.patch.object(
        requests, "get", return_value=_fake_response(payload)
    ) as get:
        validation.get_allowed_categories()
        validation.get_allowed_categories()
        validation.get_allowed_categories()
    assert get.call_count == 1


def test_validator_uses_dynamic_list_for_new_categories():
    """A category that's only in the registry (not in FALLBACK) should validate."""
    payload = {"categories": [{"id": "newdomain", "name": "Brand New"}]}
    with mock.patch.object(requests, "get", return_value=_fake_response(payload)):
        # Build minimal valid metadata using the new category.
        meta = validation.ToolkitMetadata(
            name="my-toolkit",
            version="0.1.0",
            description="x",
            author="a",
            category="newdomain",
            tools=[],
        )
    assert meta.category == "newdomain"


def test_validator_rejects_category_not_in_registry():
    payload = {"categories": [{"id": "astro", "name": "Astrophysics"}]}
    with mock.patch.object(requests, "get", return_value=_fake_response(payload)):
        with pytest.raises(Exception) as excinfo:
            validation.ToolkitMetadata(
                name="my-toolkit",
                version="0.1.0",
                description="x",
                author="a",
                category="bogus",
                tools=[],
            )
    assert "Category must be one of" in str(excinfo.value)
