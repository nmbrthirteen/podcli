import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import transcription_omnilingual as omni

PROVISION_GO = os.path.join(os.path.dirname(__file__), "..", "cli", "internal", "provision", "provision.go")


def test_python_pins_match_the_launcher():
    go = open(PROVISION_GO, encoding="utf-8").read()
    assert f'omnilingualRevision = "{omni.MODEL_REVISION}"' in go
    assert omni.MODEL_REPO in go
    for name, (sha, _) in omni.MODEL_FILES.items():
        block = re.search(rf'"{re.escape(name)}": \{{.*?SHA256: "([0-9a-f]+)"', go, re.S)
        assert block and block.group(1) == sha, name


def _fake_files(monkeypatch, payloads):
    files = {name: (omni.hashlib.sha256(data).hexdigest(), len(data)) for name, data in payloads.items()}
    monkeypatch.setattr(omni, "MODEL_FILES", files)
    fetched = []

    def urlopen(request, timeout=0):
        name = request.full_url.rsplit("/", 1)[-1]
        fetched.append(name)
        return io.BytesIO(payloads[name])

    monkeypatch.setattr(omni.urllib.request, "urlopen", urlopen)
    return fetched


def test_downloads_missing_files_and_keeps_ones_of_the_pinned_size(tmp_path, monkeypatch):
    fetched = _fake_files(monkeypatch, {"model.int8.onnx": b"weights", "tokens.txt": b"tok"})
    (tmp_path / "tokens.txt").write_bytes(b"tok")

    omni.ensure_model(str(tmp_path))

    assert fetched == ["model.int8.onnx"]
    assert (tmp_path / "model.int8.onnx").read_bytes() == b"weights"


def test_replaces_an_older_model_of_a_different_size(tmp_path, monkeypatch):
    fetched = _fake_files(monkeypatch, {"model.int8.onnx": b"new weights"})
    (tmp_path / "model.int8.onnx").write_bytes(b"old")

    omni.ensure_model(str(tmp_path))

    assert fetched == ["model.int8.onnx"]
    assert (tmp_path / "model.int8.onnx").read_bytes() == b"new weights"


def test_a_checksum_mismatch_leaves_nothing_behind(tmp_path, monkeypatch):
    _fake_files(monkeypatch, {"model.int8.onnx": b"weights"})
    monkeypatch.setattr(omni, "MODEL_FILES", {"model.int8.onnx": ("0" * 64, 7)})

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        omni.ensure_model(str(tmp_path))

    assert os.listdir(tmp_path) == []


def test_transcribing_with_a_custom_model_path_never_downloads(tmp_path, monkeypatch):
    from services import transcription as tr

    model = tmp_path / "model.int8.onnx"
    tokens = tmp_path / "tokens.txt"
    model.write_bytes(b"m")
    tokens.write_bytes(b"t")
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    monkeypatch.setattr(tr, "_omnilingual_model", lambda: str(model))
    monkeypatch.setattr(tr, "_omnilingual_tokens", lambda: str(tokens))
    monkeypatch.setattr(omni, "ensure_model", lambda *a, **k: pytest.fail("downloaded beside a custom model"))
    monkeypatch.setattr(omni, "transcribe_file", lambda *a, **k: {"transcript": "", "segments": [], "words": [], "duration": 0.0, "language": "und"})

    tr._transcribe_with_omnilingual(str(media), progress_callback=None)


def test_transcribing_with_the_managed_model_folder_ensures_the_pinned_files(tmp_path, monkeypatch):
    from services import transcription as tr

    monkeypatch.setenv("PODCLI_HOME", str(tmp_path))
    managed = tmp_path / "models" / "omnilingual"
    managed.mkdir(parents=True)
    (managed / "model.int8.onnx").write_bytes(b"m")
    (managed / "tokens.txt").write_bytes(b"t")
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(omni, "ensure_model", lambda d, *a, **k: calls.append(d))
    monkeypatch.setattr(omni, "transcribe_file", lambda *a, **k: {"transcript": "", "segments": [], "words": [], "duration": 0.0, "language": "und"})

    tr._transcribe_with_omnilingual(str(media), progress_callback=None)

    assert calls == [str(managed)]
