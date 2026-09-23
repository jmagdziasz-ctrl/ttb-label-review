"""Unit tests for extraction backend selection (extraction/factory.py):
which backend gets picked by default, and the caller-key-vs-server-key
priority/caching rules - this is exactly the logic a real bug slipped
through in (a caller-supplied key getting silently ignored) before it was
caught by manual testing, so it's worth permanent regression coverage.

Stubs out the actual OcrExtractor/VisionExtractor classes with lightweight
fakes, so these run fast with no OCR model loading, no anthropic package,
and no network calls - matching the project's existing "no OCR/model
dependencies needed" testing philosophy.

Run with: python -m pytest backend/tests
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.extraction import factory  # noqa: E402


class _FakeOcrExtractor:
    def __init__(self):
        self.method_name = "ocr"


class _FakeVisionExtractor:
    def __init__(self, api_key=None):
        self.method_name = "vision"
        self.api_key = api_key


@pytest.fixture(autouse=True)
def isolated_factory(monkeypatch):
    monkeypatch.setattr(factory, "OcrExtractor", _FakeOcrExtractor)
    monkeypatch.setattr(factory, "VisionExtractor", _FakeVisionExtractor)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    factory._cache.clear()
    yield
    factory._cache.clear()


def test_default_method_is_ocr_with_no_key_anywhere():
    assert factory.default_method() == "ocr"


def test_default_method_is_vision_with_a_caller_supplied_key():
    assert factory.default_method(api_key="sk-ant-caller") == "vision"


def test_default_method_is_vision_with_a_server_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server")
    assert factory.default_method() == "vision"


def test_get_extractor_defaults_to_ocr_and_caches_the_instance():
    first = factory.get_extractor()
    second = factory.get_extractor()
    assert first is second
    assert first.method_name == "ocr"


def test_get_extractor_with_explicit_method_overrides_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server")
    ext = factory.get_extractor("ocr")
    assert ext.method_name == "ocr"


def test_caller_supplied_key_is_used_even_without_a_server_key():
    ext = factory.get_extractor(api_key="sk-ant-caller")
    assert ext.method_name == "vision"
    assert ext.api_key == "sk-ant-caller"


def test_caller_supplied_key_is_never_cached_or_reused():
    # This is the exact bug this session found and fixed: a per-request key
    # must build its own client every time, never share the module-level
    # cache with the server's own default-key client or another caller's key.
    first = factory.get_extractor("vision", api_key="sk-ant-key-one")
    second = factory.get_extractor("vision", api_key="sk-ant-key-two")
    assert first is not second
    assert first.api_key == "sk-ant-key-one"
    assert second.api_key == "sk-ant-key-two"
    assert "vision" not in factory._cache


def test_server_default_vision_client_is_cached_and_reused(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server")
    first = factory.get_extractor()
    second = factory.get_extractor()
    assert first is second
    assert first.method_name == "vision"
    assert first.api_key is None  # built from the server's own env var, not a caller key


def test_caller_key_takes_priority_over_server_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server")
    ext = factory.get_extractor(api_key="sk-ant-caller")
    assert ext.api_key == "sk-ant-caller"


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="Unknown extraction method"):
        factory.get_extractor("not-a-real-method")
