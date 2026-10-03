import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from config.paths import paths  # noqa: E402
from services import signal_cache  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setitem(paths, "cache", str(tmp_path / "cache"))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video bytes")
    return str(video)


def test_save_and_load_round_trip(sandbox):
    signal_cache.save_signals(sandbox, energy_data=[{"t": 1.0, "v": 0.5}])
    assert signal_cache.load_signals(sandbox) == {"energy_data": [{"t": 1.0, "v": 0.5}]}


def test_load_misses_cleanly_when_nothing_cached(sandbox):
    assert signal_cache.load_signals(sandbox) == {}


def test_cache_path_is_salted_with_the_analyzer_version(sandbox, monkeypatch):
    signal_cache.save_signals(sandbox, energy_data=[{"t": 1.0}])
    path_now = signal_cache._signals_path(sandbox)
    assert os.path.exists(path_now)

    # Simulate an analyzer algorithm change: bumping the version must stop
    # serving the old profile and must not collide with its cache file.
    monkeypatch.setattr(signal_cache, "SIGNAL_CACHE_VERSION", signal_cache.SIGNAL_CACHE_VERSION + 1)
    path_next = signal_cache._signals_path(sandbox)
    assert path_next != path_now
    assert signal_cache.load_signals(sandbox) == {}
