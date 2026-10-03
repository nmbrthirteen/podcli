import json
import os
import shutil
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli as cli_mod  # noqa: E402
from config.paths import paths  # noqa: E402
from services import multicam as mc  # noqa: E402

RATE = 48000


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setitem(paths, "packed", str(home / "packed"))
    monkeypatch.setitem(paths, "working", str(tmp_path / "working"))
    monkeypatch.setitem(paths, "output", str(tmp_path / "output"))
    os.makedirs(paths["working"])
    return tmp_path


def _source(path, kind="video", **kw):
    base = dict(id=os.path.basename(path).split(".")[0], path=path, kind=kind, duration=60.0,
                has_audio=True, audio_channels=1, width=1920 if kind == "video" else 0,
                height=1080 if kind == "video" else 0, fps=30.0 if kind == "video" else 0.0)
    base.update(kw)
    return mc.Source(**base)


def test_guess_roles_uses_names_wide_and_track_numbers():
    session = mc.MulticamSession(
        session_id="abc123abc123", name="ep",
        people=[mc.Person("host", "Nika"), mc.Person("guest", "Ana")],
        sources=[
            _source("/x/A001_nika.mov"),
            _source("/x/B001.mov"),
            _source("/x/C001_wide.mov"),
            _source("/x/ZOOM0001_Tr2.WAV", kind="audio"),
            _source("/x/ZOOM0001_Tr1.WAV", kind="audio"),
            _source("/x/ZOOM0001_LR.WAV", kind="audio", audio_channels=2),
        ],
    )
    mc.guess_roles(session)
    roles = {os.path.basename(s.path): (s.role, s.person) for s in session.sources}
    assert roles["A001_nika.mov"] == ("camera", "host")
    assert roles["B001.mov"] == ("camera", "guest")
    assert roles["C001_wide.mov"] == ("camera", "wide")
    assert roles["ZOOM0001_Tr1.WAV"] == ("mic", "host")
    assert roles["ZOOM0001_Tr2.WAV"] == ("mic", "guest")
    assert roles["ZOOM0001_LR.WAV"] == ("mic", "")


def test_guess_roles_splits_a_lone_stereo_recorder_and_makes_one_camera_wide():
    session = mc.MulticamSession(
        session_id="abc123abc124", name="ep",
        people=[mc.Person("host", "Host"), mc.Person("guest", "Guest")],
        sources=[_source("/x/cam.mp4"), _source("/x/recorder.wav", kind="audio", audio_channels=2)],
    )
    mc.guess_roles(session)
    cam, rec = session.sources
    assert (cam.role, cam.person) == ("camera", "wide")
    assert rec.channel_people == ["host", "guest"]
    assert [(ch, pid) for _, ch, pid in session.person_mics()] == [(0, "host"), (1, "guest")]


def test_update_mapping_validates_roles_and_people(sandbox):
    session = mc.MulticamSession(
        session_id="abc123abc125", name="ep",
        people=[mc.Person("host", "Host"), mc.Person("guest", "Guest")],
        sources=[_source("/x/a.wav", kind="audio")],
    )
    with pytest.raises(ValueError):
        mc.update_mapping(session, {"sources": [{"id": "a", "role": "camera"}]})
    with pytest.raises(ValueError):
        mc.update_mapping(session, {"sources": [{"id": "a", "role": "mic", "person": "nobody"}]})
    mc.update_mapping(session, {"sources": [{"id": "a", "role": "mic", "person": "guest"}]})
    assert session.sources[0].guessed is False
    mc.update_mapping(session, {"people": [{"id": "host", "name": "Host"}]})
    assert session.sources[0].person == ""


def test_render_plan_fills_gaps_and_skips_removals_on_one_grid():
    session = mc.MulticamSession(session_id="abc123abc126", name="ep", cuts=[
        {"start": 10.0, "end": 12.51, "source_id": "a"},
        {"start": 12.51, "end": 14.0, "source_id": "b"},
        {"start": 15.0, "end": 16.0, "source_id": "a"},
    ])
    plan = [(p.source_id, p.out_frame, p.frames) for p in mc.render_plan(session, 30.0)]
    assert plan == [("a", 0, 75), ("b", 75, 45), (None, 120, 30), ("a", 150, 30)]

    session.removals = [{"start": 11.0, "end": 13.0}]
    plan = mc.render_plan(session, 30.0)
    assert [(p.source_id, p.out_frame, p.frames) for p in plan] == [("a", 0, 30), ("b", 30, 30), (None, 60, 30), ("a", 90, 30)]
    assert plan[1].tl_start == pytest.approx(13.0)


# --- End to end with generated media -----------------------------------------

def _speech(seconds, seed):
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    gate = np.zeros(n, dtype=np.float32)
    t = 0
    while t < n:
        burst = int(rng.uniform(0.08, 0.35) * RATE)
        gate[t:t + burst] = rng.uniform(0.3, 1.0)
        t += burst + int(rng.uniform(0.05, 0.5) * RATE)
    return rng.standard_normal(n).astype(np.float32) * gate


def _write_wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32767 * 0.5).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _mean_rgb(video, at):
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).mean(axis=0)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_sync_plan_render_and_export_a_three_camera_episode(sandbox):
    folder = sandbox / "episode"
    folder.mkdir()
    total = 60.0
    host = _speech(total, 1)
    guest = _speech(total, 2)
    t = np.arange(len(host)) / RATE
    host[(t >= 15) & (t < 30)] = 0
    host[t >= 50] = 0
    guest[t < 15] = 0
    guest[(t >= 30) & (t < 45)] = 0
    room = host + guest

    # Every device started at a different moment of the same conversation.
    starts = {"host_mic": 0.0, "guest_mic": 1.5, "nika": 3.0, "ana": 0.7, "wide": 2.2}

    def cut(x, key):
        return x[int(starts[key] * RATE):]

    _write_wav(folder / "ZOOM_Tr1.wav", cut(host + 0.08 * guest, "host_mic"))
    _write_wav(folder / "ZOOM_Tr2.wav", cut(guest + 0.08 * host, "guest_mic"))
    for name, color in (("nika", "red"), ("ana", "blue"), ("wide", "green")):
        wav = sandbox / f"{name}.wav"
        _write_wav(wav, cut(room * 0.6, name))
        seconds = total - starts[name]
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d={seconds}", "-i", str(wav),
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(folder / f"cam_{name}.mp4"))

    session = mc.new_session(folder=str(folder), people=["Nika", "Ana"])
    by_name = {os.path.basename(s.path): s for s in session.sources}
    assert (by_name["cam_nika.mp4"].role, by_name["cam_nika.mp4"].person) == ("camera", "nika")
    assert (by_name["cam_wide.mp4"].role, by_name["cam_wide.mp4"].person) == ("camera", "wide")
    assert by_name["ZOOM_Tr1.wav"].person == "nika"

    session = mc.sync_session(session)
    by_name = {os.path.basename(s.path): s for s in session.sources}
    for file, key in (("ZOOM_Tr1.wav", "host_mic"), ("ZOOM_Tr2.wav", "guest_mic"),
                      ("cam_nika.mp4", "nika"), ("cam_ana.mp4", "ana"), ("cam_wide.mp4", "wide")):
        assert abs(by_name[file].offset - starts[key]) < 0.005, (file, by_name[file].sync)

    session = mc.plan_session(session)
    ids = {s.id: os.path.basename(s.path) for s in session.sources}

    def on_air(second):
        return next(ids[c["source_id"]] for c in session.cuts if c["start"] <= second < c["end"])

    # Only Ana's camera was rolling before 2.2 s, so the episode opens on it.
    assert on_air(1.0) == "cam_ana.mp4"
    assert [on_air(s) for s in (8, 22, 38, 47, 55)] == [
        "cam_nika.mp4", "cam_ana.mp4", "cam_nika.mp4", "cam_wide.mp4", "cam_ana.mp4",
    ]

    outputs = mc.render_session(session)
    video = outputs["video"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
        check=True, capture_output=True, text=True,
    )
    expected = session.cuts[-1]["end"] - session.cuts[0]["start"]
    assert abs(float(probe.stdout) - expected) < 0.1
    offset = session.cuts[0]["start"]
    red = _mean_rgb(video, 8 - offset)
    blue = _mean_rgb(video, 22 - offset)
    assert red[0] > 150 and red[2] < 80
    assert blue[2] > 150 and blue[0] < 80
    assert len(outputs["stems"]) == 2

    premiere = ET.parse(mc.export_xml(session, "premiere")).getroot()
    assert premiere.tag == "xmeml"
    tracks = premiere.findall("./sequence/media/audio/track")
    assert len(tracks) == 2
    vclips = premiere.findall("./sequence/media/video/track/clipitem")
    assert len(vclips) == len([p for p in mc.render_plan(session, 30.0) if p.source_id])

    fcp = ET.parse(mc.export_xml(session, "fcpxml")).getroot()
    lanes = {c.get("lane") for c in fcp.iter("asset-clip")}
    assert lanes == {"1", "-1", "-2"}

    # Rendering again with nothing changed reuses the file.
    before = os.path.getmtime(video)
    assert mc.render_session(session)["video"] == video
    assert os.path.getmtime(video) == before

    # Removing 2.5 s takes it out of picture, mix, stems and both timelines alike.
    full = float(probe.stdout)
    mc.set_removals(session, [{"start": 20.0, "end": 22.5, "reason": "retake"}])
    trimmed = mc.render_session(session)

    def length(path, stream):
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
                              "stream=duration", "-of", "csv=p=0", path], check=True, capture_output=True, text=True)
        return float(out.stdout.strip().splitlines()[0])

    assert abs(length(trimmed["video"], "v:0") - (full - 2.5)) < 0.1
    assert abs(length(trimmed["video"], "a:0") - (full - 2.5)) < 0.1
    assert all(abs(length(stem, "a:0") - (full - 2.5)) < 0.1 for stem in trimmed["stems"])
    xml = ET.parse(mc.export_xml(session, "premiere")).getroot()
    assert abs(int(xml.findtext("./sequence/duration")) / 30 - (full - 2.5)) < 0.1
    mc.set_removals(session, [])

    # A hand-set offset survives a normal re-sync; force measures it again.
    wide = next(s for s in session.sources if s.path.endswith("cam_wide.mp4"))
    mc.update_mapping(session, {"sources": [{"id": wide.id, "offset": 2.5}]})
    assert mc.sync_session(session).source(wide.id).offset == 2.5
    assert abs(mc.sync_session(session, force=True).source(wide.id).offset - 2.2) < 0.005


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_a_stems_failure_does_not_strand_a_video_with_no_outputs_record(episode, monkeypatch):
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    mc.update_mapping(session, {"sources": [
        {"id": s.id, "role": "camera", "person": "nika" if "one" in os.path.basename(s.path) else "ana"}
        for s in session.sources if s.kind == "video"
    ]})
    session = mc.plan_session(mc.sync_session(session))
    out_dir = mc._output_dir(session)

    monkeypatch.setattr(mc, "_render_stems", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        mc.render_session(session)

    # Nothing half-rendered should land at the canonical output path, and the
    # session must not record a video it never finished publishing.
    assert not (out_dir / "episode.mp4").exists()
    assert not any(out_dir.glob("*.publishing"))
    session = mc.MulticamSession.load(session.session_id)
    assert not session.outputs.get("video")

    monkeypatch.undo()
    outputs = mc.render_session(session)
    assert os.path.exists(outputs["video"]) and len(outputs["stems"]) == 2
    assert not any(out_dir.glob("*.publishing"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_a_rerender_keeps_stable_paths_and_drops_stems_nobody_needs(episode):
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    mc.update_mapping(session, {"sources": [
        {"id": s.id, "role": "camera", "person": "nika" if "one" in os.path.basename(s.path) else "ana"}
        for s in session.sources if s.kind == "video"
    ]})
    session = mc.plan_session(mc.sync_session(session))
    out_dir = mc._output_dir(session)
    first = mc.render_session(session)
    assert first["video"] == str(out_dir / "episode.mp4")

    stale = out_dir / "former-guest.wav"
    stale.write_bytes(b"old stem")
    session = mc.MulticamSession.load(session.session_id)
    session.outputs = {**session.outputs, "stems": [*session.outputs["stems"], str(stale)], "render_key": ""}
    session.save()

    second = mc.render_session(session)
    assert second["video"] == first["video"]
    assert not stale.exists()
    assert all(os.path.exists(p) for p in second["stems"])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_sync_measures_clock_drift(sandbox):
    folder = sandbox / "drift"
    folder.mkdir()
    voice = _speech(180, 3)
    speed = 1.0 + 150e-6
    # The second recorder's clock runs fast: it captures the same sound in fewer samples.
    t = np.arange(int(len(voice) / speed)) * speed
    drifting = np.interp(t, np.arange(len(voice)), voice).astype(np.float32)
    _write_wav(folder / "a_Tr1.wav", voice)
    _write_wav(folder / "a_Tr2.wav", drifting[int(4 * RATE):])

    session = mc.sync_session(mc.new_session(folder=str(folder)))
    moved = next(s for s in session.sources if s.path.endswith("Tr2.wav"))
    assert moved.sync["status"] == "ok"
    assert abs(moved.sync["drift_ppm"] - 150) < 15
    assert abs(moved.offset - 4.0) < 0.005


def test_sync_review_reasons_flag_a_fit_that_looks_clean_but_isnt():
    import services.multicam_signal as sig

    clean = sig.ClockFit(offset=0.0, speed=1.0, residual_ms=5.0, checkpoints=5, total_checkpoints=5,
                          residual_all_ms=5.0, speed_fallback=False)
    good_match = sig.CoarseMatch(lag_seconds=0.0, score=30.0, peak_ratio=0.1)
    assert mc._sync_review_reasons(clean, good_match, overlap_seconds=1200.0) == []

    # Speed was forced back to 1.0: flagged for that even though the inlier
    # residual (5 ms) is tidy.
    bad_fallback = sig.ClockFit(offset=0.0, speed=1.0, residual_ms=5.0, checkpoints=2, total_checkpoints=2,
                                 residual_all_ms=500.0, speed_fallback=True)
    reasons = mc._sync_review_reasons(bad_fallback, good_match, overlap_seconds=60.0)
    assert any("implausible" in r for r in reasons)

    # One checkpoint was a real outlier and got correctly dropped (4 of 5
    # survive, under the 40% dropped threshold): residual_all_ms is large
    # only because it still includes that outlier. The fit the sync actually
    # used is the tidy inlier one, so this must not flag for review.
    one_outlier_dropped = sig.ClockFit(offset=0.0, speed=1.0, residual_ms=5.0, checkpoints=4, total_checkpoints=5,
                                        residual_all_ms=500.0, speed_fallback=False)
    assert mc._sync_review_reasons(one_outlier_dropped, good_match, overlap_seconds=60.0) == []

    # Too few checkpoints over a long overlap, and most checkpoints dropped.
    sparse = sig.ClockFit(offset=0.0, speed=1.0, residual_ms=1.0, checkpoints=1, total_checkpoints=6,
                           residual_all_ms=1.0, speed_fallback=False)
    reasons = mc._sync_review_reasons(sparse, good_match, overlap_seconds=900.0)
    assert any("checkpoints" in r and "outliers" in r for r in reasons)
    assert any("10+ minute overlap" in r for r in reasons)

    # A weak correlation peak alone should trigger review even if the fit is tidy.
    weak_match = sig.CoarseMatch(lag_seconds=0.0, score=30.0, peak_ratio=0.7)
    reasons = mc._sync_review_reasons(clean, weak_match, overlap_seconds=60.0)
    assert any("correlation" in r for r in reasons)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_shared_audio_falls_back_to_diarization(sandbox, monkeypatch):
    from services import speaker_detection

    folder = sandbox / "single"
    folder.mkdir()
    _write_wav(sandbox / "room.wav", _speech(30, 4))
    for name, color in (("a", "red"), ("b", "blue")):
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30:d=30", "-i", str(sandbox / "room.wav"),
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / f"cam_{name}.mp4"))
    turns = [
        {"speaker": "SPEAKER_01", "start": 0.5, "end": 12.0},
        {"speaker": "SPEAKER_00", "start": 12.0, "end": 28.0},
    ]
    monkeypatch.setattr(speaker_detection, "run_diarization", lambda *a, **k: turns)

    session = mc.new_session(folder=str(folder), people=[{"name": "Host"}, {"name": "Guest"}])
    session = mc.sync_session(session)
    assert not session.person_mics()
    session = mc.plan_session(session)

    # First voice heard maps to the first person, whose camera is cam_a.
    assert session.speaker_map == {"SPEAKER_01": "host", "SPEAKER_00": "guest"}
    ids = {s.id: os.path.basename(s.path) for s in session.sources}
    assert [ids[c["source_id"]] for c in session.cuts] == ["cam_a.mp4", "cam_b.mp4"]


def test_mapping_changes_drop_the_cut_but_a_new_look_keeps_it(sandbox):
    session = mc.MulticamSession(
        session_id="abc123abc140", name="ep",
        people=[mc.Person("host", "Host"), mc.Person("guest", "Guest")],
        sources=[_source("/x/a.mp4", role="camera", person="host", offset=0.0)],
        cuts=[{"start": 0, "end": 5, "source_id": "a"}],
    )
    mc.update_mapping(session, {"look": "warm"})
    assert session.cuts
    mc.update_mapping(session, {"sources": [{"id": "a", "role": "camera", "person": "host"}]})
    assert session.cuts, "confirming an unchanged role keeps the cut"
    mc.update_mapping(session, {"sources": [{"id": "a", "person": "guest"}]})
    assert session.cuts == []


def test_phone_frame_rates_snap_to_broadcast_rates():
    assert mc._standard_fps(29.92) == pytest.approx(30000 / 1001)
    assert mc._standard_fps(30.02) == 30.0
    assert mc._standard_fps(24.0) == 24.0
    assert mc._standard_fps(12.0) == 12.0


def test_scan_refuses_a_folder_far_bigger_than_one_episode(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "MAX_SCANNED_ENTRIES", 50)
    for i in range(60):
        (tmp_path / f"note{i}.txt").write_text("x")
    with pytest.raises(ValueError, match="too many files"):
        mc.scan_folder(str(tmp_path))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_sync_counts_audio_that_starts_late_inside_a_video(sandbox):
    folder = sandbox / "late"
    folder.mkdir()
    voice = _speech(60, 7)
    _write_wav(folder / "mic_Tr1.wav", voice)
    _write_wav(sandbox / "cam.wav", voice[int(2.0 * RATE):])
    # The camera's audio stream starts 0.4 s after its picture, as phones and OBS often do.
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=160x90:r=30:d=58", "-itsoffset", "0.4", "-i", str(sandbox / "cam.wav"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / "cam_wide.mp4"))
    session = mc.sync_session(mc.new_session(folder=str(folder)))
    cam = next(s for s in session.sources if s.path.endswith("cam_wide.mp4"))
    mic = next(s for s in session.sources if s.path.endswith("mic_Tr1.wav"))
    # Picture time zero sits 0.4 s before the camera heard second 2.0 of the voice.
    assert abs((cam.offset - mic.offset) - 1.6) < 0.01


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_exports_keep_stereo_sides_apart_and_honor_camera_timecode(sandbox):
    folder = sandbox / "stereo"
    folder.mkdir()
    host, guest = _speech(40, 8), _speech(40, 9)
    t = np.arange(len(host)) / RATE
    host[t >= 20] = 0
    guest[t < 20] = 0
    stereo = np.stack([host + 0.05 * guest, guest + 0.05 * host], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 16000).astype(np.int16)
    with wave.open(str(folder / "recorder.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    _write_wav(sandbox / "room.wav", host + guest)
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=160x90:r=25:d=40", "-i", str(sandbox / "room.wav"),
            "-timecode", "01:00:00:00", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(folder / "cam.mov"))

    session = mc.new_session(folder=str(folder))
    rec = next(s for s in session.sources if s.path.endswith("recorder.wav"))
    cam = next(s for s in session.sources if s.path.endswith("cam.mov"))
    assert rec.channel_people == ["host", "guest"] and cam.timecode == 3600.0
    session = mc.plan_session(mc.sync_session(session))

    premiere = ET.parse(mc.export_xml(session, "premiere")).getroot()
    indexes = [t.findtext("clipitem/sourcetrack/trackindex") for t in premiere.findall("./sequence/media/audio/track")]
    assert indexes == ["1", "2"]

    fcp = ET.parse(mc.export_xml(session, "fcpxml")).getroot()
    assert {c.get("srcCh") for c in fcp.iter("audio-channel-source")} == {"1", "2"}
    cam_asset = next(a for a in fcp.iter("asset") if a.get("name") == "cam.mov")
    assert cam_asset.get("start") == "3600s"
    cam_clips = [c for c in fcp.iter("asset-clip") if c.get("lane") == "1"]
    assert all(Fraction(c.get("start").rstrip("s")) >= 3600 for c in cam_clips)


def test_exports_point_a_second_audio_stream_at_the_right_track_not_channel_one(sandbox):
    """A mic file with one mono stream per mic (a camera's second XLR input, say).

    audio_stream_index picks the stream; the editor timelines must land the
    mic's track/channel at the position that stream actually sits at in the
    file, not always at channel 1.
    """
    cam = _source("cam.mp4", role="camera", person="host", offset=0.0, duration=40.0)
    mic = _source("mic.mp4", kind="video", role="mic", person="host", offset=0.0, duration=40.0,
                  audio_stream_count=2, audio_stream_channels=[1, 1], audio_stream_index=1)
    session = mc.MulticamSession(session_id="abc123abc905", name="ep", people=[mc.Person("host", "Host")],
                                 sources=[cam, mic], reference_id="mic",
                                 cuts=[{"start": 0.0, "end": 40.0, "source_id": "cam"}])

    premiere = ET.parse(mc.export_xml(session, "premiere")).getroot()
    assert premiere.findtext("./sequence/media/audio/track/clipitem/sourcetrack/trackindex") == "2"
    mic_file = next(f for f in premiere.iter("file") if f.findtext("name") == "mic.mp4")
    assert mic_file.findtext("media/audio/channelcount") == "2"

    fcp = ET.parse(mc.export_xml(session, "fcpxml")).getroot()
    assert {c.get("srcCh") for c in fcp.iter("audio-channel-source")} == {"2"}
    mic_asset = next(a for a in fcp.iter("asset") if a.get("name") == "mic.mp4")
    assert mic_asset.get("audioChannels") == "2"


# --- podcli multicam ------------------------------------------------------------

def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["podcli", "multicam", *argv])
    monkeypatch.setattr(cli_mod, "_auto_migrate_cli", lambda args: None)
    monkeypatch.setenv("PODCLI_NO_ONBOARDING", "1")
    try:
        cli_mod.main()
    except SystemExit as e:
        return e.code or 0
    return 0


@pytest.fixture
def episode(sandbox):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg not installed")
    folder = sandbox / "ep"
    folder.mkdir()
    a = _speech(40, 5)
    b = _speech(40, 6)
    t = np.arange(len(a)) / RATE
    a[t >= 20] = 0
    b[t < 20] = 0
    _write_wav(folder / "rec_Tr1.wav", a + 0.08 * b)
    _write_wav(folder / "rec_Tr2.wav", (b + 0.08 * a)[RATE:])
    for name, color in (("one", "red"), ("two", "blue")):
        _write_wav(sandbox / f"{name}.wav", (a + b) * 0.6)
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30:d=40", "-i", str(sandbox / f"{name}.wav"),
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / f"cam_{name}.mp4"))
    return folder


def test_set_parses_numbers_prefixes_and_stereo_pairs(sandbox):
    session = mc.MulticamSession(
        session_id="abc123abc130", name="ep",
        people=[mc.Person("nika", "Nika"), mc.Person("ana", "Ana")],
        sources=[
            mc.Source(id="a", path="/x/cam_a.mp4", kind="video", duration=9, has_audio=True, audio_channels=1),
            mc.Source(id="b", path="/x/zoom_lr.wav", kind="audio", duration=9, has_audio=True, audio_channels=2),
        ],
    )
    session = cli_mod._apply_multicam_fixes(session, ["1=camera:Ana", "zoom=mic:nika,ana"])
    cam, rec = session.sources
    assert (cam.role, cam.person) == ("camera", "ana")
    assert rec.channel_people == ["nika", "ana"]
    with pytest.raises(ValueError, match="Unknown person"):
        cli_mod._apply_multicam_fixes(session, ["1=camera:bob"])
    with pytest.raises(ValueError, match="Can't read"):
        cli_mod._apply_multicam_fixes(session, ["1=projector"])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_one_command_edits_the_episode_and_reruns_resume(episode, monkeypatch, capsys):
    code = run_cli(monkeypatch, str(episode), "--people", "Nika, Ana",
                   "--set", "cam_one=camera:nika", "--set", "cam_two=camera:ana", "--export", "premiere")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "shots over" in out and "episode.mp4" in out and "episode-premiere.xml" in out

    [summary] = mc.list_sessions()
    session = mc.MulticamSession.load(summary["session_id"])
    assert not mc.needs_sync(session)
    assert os.path.exists(session.outputs["video"])

    # A second run on the same folder reopens the edit and skips sync and cutting.
    monkeypatch.setattr(mc, "sync_session", lambda *a, **k: pytest.fail("synced again"))
    monkeypatch.setattr(mc, "plan_session", lambda *a, **k: pytest.fail("cut again"))
    code = run_cli(monkeypatch, str(episode), "--no-render", "--export", "fcpxml")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "episode.fcpxml" in out
    assert len(mc.list_sessions()) == 1

    assert run_cli(monkeypatch, str(episode), "--delete") == 0
    assert "Deleted" in capsys.readouterr().out
    assert mc.list_sessions() == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_bad_fix_exits_with_a_readable_error(episode, monkeypatch, capsys):
    code = run_cli(monkeypatch, str(episode), "--set", "cam=camera", "--no-render")
    assert code == 1
    assert "matches 2 files" in capsys.readouterr().err


def test_no_target_prints_usage(monkeypatch, capsys):
    assert run_cli(monkeypatch) == 2
    assert "podcli multicam <folder" in capsys.readouterr().out


def test_set_cuts_checks_coverage_and_merges(sandbox):
    session = mc.MulticamSession(
        session_id="abc123abc150", name="ep",
        people=[mc.Person("host", "Host"), mc.Person("guest", "Guest")],
        sources=[
            _source("/x/a.mp4", role="camera", person="host", offset=0.0),
            _source("/x/w.mp4", role="camera", person="wide", offset=10.0, duration=20.0),
        ],
    )
    mc.set_cuts(session, [
        {"start": 0, "end": 5, "source_id": "a"},
        {"start": 5, "end": 12, "source_id": "a"},
        {"start": 12, "end": 20, "source_id": "w"},
    ])
    assert session.cuts == [{"start": 0, "end": 12, "source_id": "a"}, {"start": 12, "end": 20, "source_id": "w"}]
    with pytest.raises(ValueError, match="wasn't recording"):
        mc.set_cuts(session, [{"start": 0, "end": 12, "source_id": "w"}])
    with pytest.raises(ValueError, match="back to back"):
        mc.set_cuts(session, [{"start": 0, "end": 4, "source_id": "a"}, {"start": 5, "end": 9, "source_id": "a"}])


def test_cli_json_mode_applies_a_cut_and_builds_the_preview(episode, monkeypatch, capsys, tmp_path):
    code = run_cli(monkeypatch, str(episode), "--people", "Nika, Ana", "--set", "cam_one=camera:nika",
                   "--set", "cam_two=camera:ana", "--no-render", "--json", "--activity")
    out = capsys.readouterr().out
    assert code == 0, out
    data = json.loads(out)
    assert data["cuts"] and set(data["activity"]["people"]) == {"nika", "ana"}
    assert data["activity"]["people"]["nika"][0][0] < 20 <= data["activity"]["people"]["ana"][0][0] + 1

    one = next(s["id"] for s in data["sources"] if s["name"] == "cam_one.mp4")
    start, end = data["cuts"][0]["start"], data["cuts"][-1]["end"]
    cuts = tmp_path / "cuts.json"
    cuts.write_text(json.dumps([{"start": start, "end": end, "source_id": one}]))
    code = run_cli(monkeypatch, data["session_id"], "--cuts", str(cuts), "--preview", "--no-render", "--json")
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["cuts"] == [{"start": start, "end": end, "source_id": one}]
    assert set(data["preview"]["proxies"]) == {s["id"] for s in data["sources"] if s["role"] == "camera"}
    assert os.path.exists(data["preview"]["audio"])

    # Moving a mic changes the mix, so the old preview audio no longer matches.
    session = mc.MulticamSession.load(data["session_id"])
    mic = next(s for s in session.sources if s.role in ("mic", "camera") and s.offset)
    mc.update_mapping(session, {"sources": [{"id": mic.id, "nudge": 0.04}]})
    assert mc.payload(mc.MulticamSession.load(data["session_id"]))["preview"] is None

    cuts.write_text("not json")
    assert run_cli(monkeypatch, data["session_id"], "--cuts", str(cuts), "--json") == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_preview_stills_regenerate_after_a_nudge_instead_of_serving_a_stale_frame(episode):
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    mc.update_mapping(session, {"sources": [
        {"id": s.id, "role": "camera", "person": "nika" if "one" in os.path.basename(s.path) else "ana"}
        for s in session.sources if s.kind == "video"
    ]})
    session = mc.sync_session(session)
    cam = next(s for s in session.sources if os.path.basename(s.path) == "cam_one.mp4")

    frames = mc.previews(session, looks=True, at=cam.timeline_start() + 1.0)
    before = frames["cameras"][cam.id]
    look_before = frames["looks"][cam.id]["natural"]
    assert os.path.exists(before) and os.path.exists(look_before)

    # Nudging changes the offset->source mapping for the same timeline moment,
    # so the cache key must change and a fresh still must be rendered: reusing
    # the old file would show the pre-nudge frame.
    mc.update_mapping(session, {"sources": [{"id": cam.id, "nudge": 2.0}]})
    session = mc.MulticamSession.load(session.session_id)
    cam = session.source(cam.id)
    frames = mc.previews(session, looks=True, at=cam.timeline_start() + 1.0)
    after = frames["cameras"][cam.id]
    look_after = frames["looks"][cam.id]["natural"]
    assert after != before and os.path.exists(after)
    assert look_after != look_before and os.path.exists(look_after)
    # The pre-nudge stills aren't left behind: every nudge would otherwise
    # pile up one more frame and one more look per camera, forever.
    assert not os.path.exists(before)
    assert not os.path.exists(look_before)


def test_reopening_a_session_with_an_unchanged_camera_keeps_its_sync(episode):
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    session = mc.sync_session(session)
    cam = next(s for s in session.sources if os.path.basename(s.path) == "cam_one.mp4")
    assert cam.synced

    reopened = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    assert reopened.session_id == session.session_id
    assert reopened.source(cam.id).synced
    assert reopened.source(cam.id).offset == cam.offset


def test_source_changed_on_disk_treats_a_zero_zero_identity_as_unknown(tmp_path):
    path = tmp_path / "f.mp4"
    path.write_bytes(b"x")
    s = _source(str(path), file_size=0, file_mtime_ns=0)
    assert mc._source_changed_on_disk(s) is False


def test_reopening_an_old_shape_session_backfills_identity_without_resetting_sync(episode):
    """A session saved before file_size/file_mtime_ns existed has both at 0.

    That must read as "no fingerprint recorded yet", not "the file shrank to
    nothing": reopening it should stamp the real identity in and leave sync,
    cuts and range exactly as they were.
    """
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    session = mc.sync_session(session)
    session = mc.plan_session(session)
    cuts_before = session.cuts
    cam = next(s for s in session.sources if os.path.basename(s.path) == "cam_one.mp4")
    offset_before = cam.offset
    assert cuts_before and cam.synced

    # Simulate the old session shape directly on disk, as if saved before
    # file identity was tracked.
    for s in session.sources:
        s.file_size = s.file_mtime_ns = 0
    session.save()

    reopened = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    assert reopened.cuts == cuts_before
    fresh = reopened.source(cam.id)
    assert fresh.synced and fresh.offset == offset_before
    assert fresh.file_size != 0 and fresh.file_mtime_ns != 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_refresh_stale_sources_copies_every_probe_field_and_resets_its_tiles(sandbox):
    """Re-probing a replaced file used to only copy duration/size/fps.

    A call recording that gained a second audio stream, or lost the one a
    tile's audio_stream_index pointed at, kept the stale stream count and an
    out-of-range index; a pane cropped out of it kept showing as synced
    because nothing told the virtual camera built from it to refresh too.
    """
    path = sandbox / "call.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=s=160x90:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path))
    src = mc.probe_source(str(path))
    assert src.audio_stream_count == 1
    src.role, src.person, src.offset, src.audio_stream_index = "camera", "host", 0.0, 0
    pane = _source(str(path), role="camera", person="pane1", parent=src.id, crop=[0.0, 0.0, 0.5, 1.0],
                   offset=5.0, duration=2.0, sync={"status": "ok"})
    session = mc.MulticamSession(session_id="abc123abc906", name="ep",
                                 people=[mc.Person("host", "Host")], sources=[src, pane])

    # Re-export with a second audio stream: same path, new bytes.
    _ffmpeg("-f", "lavfi", "-i", "color=s=160x90:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2", "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(path))

    assert mc.refresh_stale_sources(session)
    assert src.audio_stream_count == 2
    assert src.audio_stream_channels == [1, 1]
    assert src.audio_stream_index == 0  # still in range, so left alone
    assert src.offset is None and src.sync["status"] == "failed"
    # The pane cropped from it is no longer synced either, not left showing stale.
    assert pane.offset is None and pane.sync["status"] == "failed"
    assert pane.sync == src.sync


def test_reopening_a_session_detects_a_camera_replaced_on_disk(episode):
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    session = mc.sync_session(session)
    cam_path = episode / "cam_one.mp4"
    cam = next(s for s in session.sources if os.path.basename(s.path) == "cam_one.mp4")
    assert cam.synced
    old_identity = (cam.file_size, cam.file_mtime_ns)

    # Re-export the same file path with different content: same name, same id
    # (basename:size can coincide), but the bytes underneath changed.
    _ffmpeg("-f", "lavfi", "-i", "color=c=yellow:s=160x90:r=30:d=40", "-i", str(episode.parent / "one.wav"),
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(cam_path))

    reopened = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    assert reopened.session_id == session.session_id
    fresh = reopened.source(cam.id)
    assert (fresh.file_size, fresh.file_mtime_ns) != old_identity
    assert not fresh.synced
    assert fresh.sync["status"] == "failed"
    assert reopened.cuts == []


def test_loading_by_session_id_also_catches_a_camera_replaced_on_disk(episode, monkeypatch):
    """The MCP tool and the CLI jump straight to a known session id instead of

    reopening by folder, so they used to skip the stale-file check entirely:
    a camera swapped out underneath an open session kept its old sync and cut.
    """
    import main as backend

    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    session = mc.sync_session(session)
    session = mc.plan_session(session)
    cam_path = episode / "cam_one.mp4"
    cam = next(s for s in session.sources if os.path.basename(s.path) == "cam_one.mp4")
    assert cam.synced and session.cuts

    _ffmpeg("-f", "lavfi", "-i", "color=c=yellow:s=160x90:r=30:d=40", "-i", str(episode.parent / "one.wav"),
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(cam_path))

    sent = []
    monkeypatch.setattr(backend, "emit_result", lambda task, status, data=None, error=None: sent.append((status, data, error)))
    backend.handle_manage_multicam("t", {"action": "show", "session_id": session.session_id})
    assert sent[0][0] == "success", sent
    fresh = next(s for s in sent[0][1]["sources"] if s["id"] == cam.id)
    assert fresh["sync"]["status"] == "failed"
    assert sent[0][1]["cuts"] == []


def test_last_person_is_the_guest_and_roles_survive_renames(episode):
    session = mc.new_session(folder=str(episode), people=["Nihal", "Cameron", "Ana"])
    assert [(p.name, p.role) for p in session.people] == [("Nihal", "host"), ("Cameron", "host"), ("Ana", "guest")]
    session = mc.rename_people(session, ["Nihal", "Cam", "Ana Smith"])
    assert [p.role for p in session.people] == ["host", "host", "guest"]


def test_cli_guests_flag_sets_roles(episode, monkeypatch, capsys):
    code = run_cli(monkeypatch, str(episode), "--people", "Nika, Ana", "--guests", "Nika",
                   "--set", "cam_one=camera:nika", "--set", "cam_two=camera:ana", "--no-render", "--json")
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert {p["name"]: p["role"] for p in data["people"]} == {"Nika": "guest", "Ana": "host"}
    assert run_cli(monkeypatch, data["session_id"], "--guests", "Bob", "--no-render") == 1
    assert "No person named 'bob'" in capsys.readouterr().err


def test_a_saved_edit_renders_on_another_machine_without_syncing(episode, monkeypatch, capsys, tmp_path):
    assert run_cli(monkeypatch, str(episode), "--people", "Nika, Ana", "--set", "cam_one=camera:nika",
                   "--set", "cam_two=camera:ana", "--no-render", "--json") == 0
    prepared = json.loads(capsys.readouterr().out)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(prepared))

    # A fresh worker: the same recordings in another folder, and nothing saved.
    elsewhere = tmp_path / "worker" / "ep"
    shutil.copytree(episode, elsewhere)
    monkeypatch.setitem(paths, "packed", str(tmp_path / "worker" / "home" / "packed"))
    monkeypatch.setattr(mc, "sync_session", lambda *a, **k: pytest.fail("synced again"))
    monkeypatch.setattr(mc, "plan_session", lambda *a, **k: pytest.fail("cut again"))
    assert run_cli(monkeypatch, str(elsewhere), "--state", str(state), "--no-render", "--json") == 0
    restored = json.loads(capsys.readouterr().out)

    assert restored["session_id"] != prepared["session_id"]
    assert {s["id"]: s["offset"] for s in restored["sources"]} == {s["id"]: s["offset"] for s in prepared["sources"]}
    assert restored["cuts"] == prepared["cuts"]


def test_transcript_credits_each_word_to_the_mic_that_spoke(episode, monkeypatch):
    from services import transcription

    calls = []

    def fake(path, **kw):
        calls.append(path)
        return {"words": [{"word": "hello", "start": 2.0, "end": 2.4}, {"word": "there", "start": 25.0, "end": 25.5}],
                "language": "en"}

    monkeypatch.setattr(transcription, "transcribe_file", fake)
    session = mc.new_session(folder=str(episode), people=["Nika", "Ana"])
    mc.update_mapping(session, {"sources": [
        {"id": s.id, "role": "camera", "person": "nika" if "one" in os.path.basename(s.path) else "ana"}
        for s in session.sources if s.kind == "video"
    ]})
    session = mc.sync_session(session)
    data = mc.transcript(session)
    assert [(w["text"], w["person"]) for w in data["words"]] == [("hello", "nika"), ("there", "ana")]
    assert mc.transcript(session) == data and len(calls) == 1


def test_turn_edges_follow_sentences_not_loose_timestamps():
    def words(spec):
        return [{"text": t, "person": p, "start": i, "end": i + 0.5} for i, (t, p) in enumerate(spec)]

    # "Right," opens the host's sentence but was timed before the guest stopped.
    w = words([("get", "g"), ("home.", "g"), ("Right,", "g"), ("because", "h"), ("every", "h")])
    mc._settle_turn_edges(w, [0.0] * len(w))
    assert [x["person"] for x in w] == ["g", "g", "h", "h", "h"]

    # A lone word mid-sentence credited to the other mic goes back to the speaker.
    w = words([("the", "g"), ("pain", "h"), ("starts", "g"), ("here.", "g")])
    mc._settle_turn_edges(w, [0.0] * len(w))
    assert [x["person"] for x in w] == ["g", "g", "g", "g"]

    # A word the mics credit clearly never moves, even at a turn edge.
    w = words([("home.", "a"), ("Then", "a"), ("Exactly.", "b")])
    mc._settle_turn_edges(w, [0.0, 15.0, 15.0])
    assert [x["person"] for x in w] == ["a", "a", "b"]

    # Georgian has no letter case, so a sentence opener there can't pass an
    # isupper() check. It must still move to the next speaker.
    w = words([("დამიდა.", "g"), ("კარგი,", "g"),
               ("ამიტაო", "h")])
    mc._settle_turn_edges(w, [0.0] * len(w))
    assert [x["person"] for x in w] == ["g", "h", "h"]


def test_sentence_opener_detection_handles_caseless_scripts():
    assert mc._looks_like_sentence_opener("კარგი")  # Georgian, no case
    assert mc._looks_like_sentence_opener("Right,")
    assert not mc._looks_like_sentence_opener("right,")
    assert not mc._looks_like_sentence_opener("123")
    assert not mc._looks_like_sentence_opener("")


# --- Remote recordings ------------------------------------------------------------

def _voices(seconds=40):
    host, guest = _speech(seconds, 21), _speech(seconds, 22)
    t = np.arange(len(host)) / RATE
    host[t >= 8] = 0           # a short question...
    guest[t < 8] = 0           # ...then one long answer
    return host, guest


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_one_file_per_person_cuts_like_a_call(sandbox):
    folder = sandbox / "remote"
    folder.mkdir()
    host, guest = _voices()
    # Each person's local recording hears only them (headphones), so the files share no sound.
    for name, voice, color in (("nika", host, "red"), ("ana", guest, "blue")):
        _write_wav(sandbox / f"{name}.wav", voice)
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d=40", "-i", str(sandbox / f"{name}.wav"),
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / f"{name}.mp4"))
    session = mc.sync_session(mc.new_session(folder=str(folder), people=["Nika", "Ana"]))
    assert {s.sync["status"] for s in session.sources if not s.virtual} == {"reference", "assumed"}
    split = next(s for s in session.sources if s.members)
    assert split.person == "wide" and mc.resolved_style(session) == "remote"
    assert sorted(p for _, _, p in session.person_mics()) == ["ana", "nika"]

    session = mc.plan_session(session)
    ids = {s.id: s for s in session.sources}
    # The question plays on the split; the answer goes to Ana once she's 4 s in.
    assert ids[session.cuts[0]["source_id"]].members
    guest_shot = next(c for c in session.cuts if ids[c["source_id"]].person == "ana")
    assert 11.0 < guest_shot["start"] < 13.5

    video = mc.render_session(session, stems=False)["video"]
    left, right = _mean_rgb_at(video, 3, crop="crop=iw/2:ih:0:0"), _mean_rgb_at(video, 3, crop="crop=iw/2:ih:iw/2:0")
    assert left[0] > 150 and right[2] > 150  # split screen: Nika red on the left, Ana blue on the right
    full = _mean_rgb_at(video, session.cuts[-1]["end"] - session.cuts[0]["start"] - 2)
    assert full[2] > 150 and full[0] < 80  # Ana alone, full frame


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_one_gallery_recording_becomes_a_camera_per_tile(sandbox, monkeypatch):
    from services import speaker_detection

    folder = sandbox / "gallery"
    folder.mkdir()
    host, guest = _voices()
    _write_wav(sandbox / "call.wav", host + guest)
    # A two-tile call recording: moving pictures in each tile, a flat gutter between them.
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=310x360:r=30:d=40", "-f", "lavfi", "-i", "mandelbrot=s=310x360:r=30",
            "-i", str(sandbox / "call.wav"),
            "-filter_complex", "[0:v]pad=320:360:0:0:color=0x202020[l];[1:v]pad=320:360:10:0:color=0x202020[r];[l][r]hstack[v]",
            "-map", "[v]", "-map", "2:a", "-t", "40", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(folder / "zoom_call.mp4"))
    monkeypatch.setattr(speaker_detection, "run_diarization", lambda *a, **k: [
        {"speaker": "SPEAKER_00", "start": 0.5, "end": 7.8}, {"speaker": "SPEAKER_01", "start": 8.2, "end": 39.5}])

    session = mc.new_session(folder=str(folder), people=["Nika", "Ana"])
    panes = [s for s in session.sources if s.parent]
    assert [p.person for p in panes] == ["nika", "ana"]
    assert panes[0].crop[0] < 0.05 and 0.45 < panes[0].crop[2] < 0.52 and panes[1].crop[0] > 0.48
    session = mc.plan_session(mc.sync_session(session))
    assert mc.resolved_style(session) == "remote"
    ids = {s.id: s for s in session.sources}
    assert ids[session.cuts[0]["source_id"]].person == "wide"
    assert ids[session.cuts[-1]["source_id"]].person == "ana"
    mc.render_session(session, stems=False)


def _mean_rgb_at(video, at, crop="null"):
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at}", "-i", str(video),
         "-frames:v", "1", "-vf", f"{crop},scale=8:8", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).mean(axis=0)


# --- Audit regressions ------------------------------------------------------------

def _bare(sandbox, n=2):
    return mc.MulticamSession(
        session_id="abc123abc199", name="ep",
        people=[mc.Person("host", "Host"), mc.Person("guest", "Guest", role="guest")],
        sources=[_source(f"/x/{c}.mp4", role="camera", person=p, offset=0.0, id=c)
                 for c, p in (("a", "host"), ("b", "guest"), ("w", "wide"))[:n + 1]],
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "x"])
def test_non_finite_numbers_are_refused_everywhere(sandbox, bad):
    session = _bare(sandbox)
    session.cuts = [{"start": 0, "end": 30, "source_id": "a"}]
    with pytest.raises(ValueError):
        mc.set_removals(session, [{"start": 1, "end": bad}])
    with pytest.raises(ValueError):
        mc.set_cuts(session, [{"start": 0, "end": bad, "source_id": "a"}])
    with pytest.raises(ValueError):
        mc.update_mapping(session, {"sources": [{"id": "a", "offset": bad}]})
    with pytest.raises(ValueError):
        mc.update_mapping(session, {"range_end": bad})


def test_choosing_a_style_keeps_only_real_tweaks(sandbox):
    session = _bare(sandbox)
    mc.update_mapping(session, {"cut_settings": {**mc.STYLES["studio"], "style": "remote"}})
    # Everything sent equals studio defaults, which differ from remote's: those are tweaks.
    assert session.cut_settings["style"] == "remote" and session.cut_settings["host_solo"] is True
    mc.update_mapping(session, {"cut_settings": {"style": "studio"}})
    assert session.cut_settings == {"style": "studio"}
    mc.update_mapping(session, {"cut_settings": {"min_shot": 2.0, "max_shot": 12}})
    assert session.cut_settings == {"style": "studio", "max_shot": 12.0}


def test_removals_and_cuts_are_checked_against_each_other(sandbox):
    session = _bare(sandbox)
    session.cuts = [{"start": 0, "end": 30, "source_id": "a"}]
    mc.set_removals(session, [{"start": 9, "end": 21}])
    with pytest.raises(ValueError, match="cover this whole cut"):
        mc.set_cuts(session, [{"start": 10, "end": 20, "source_id": "a"}])
    assert session.cuts == [{"start": 0, "end": 30, "source_id": "a"}]


def test_frame_size_comes_from_one_landscape_camera(sandbox):
    session = _bare(sandbox, n=1)
    session.sources[1].width, session.sources[1].height = 1080, 1920
    assert mc._output_format(session)[:2] == (1920, 1080)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_studio_cameras_that_start_at_different_times_stay_studio(sandbox):
    folder = sandbox / "studio"
    folder.mkdir()
    host, guest = _voices()
    room = (host + guest) * 0.6
    _write_wav(folder / "rec_Tr1.wav", host + 0.08 * guest)
    _write_wav(folder / "rec_Tr2.wav", guest + 0.08 * host)
    for name, start in (("nika", 0), ("ana", 12)):
        _write_wav(sandbox / f"{name}.wav", room[int(start * RATE):])
        _ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s=160x90:r=30:d={40 - start}", "-i", str(sandbox / f"{name}.wav"),
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / f"cam_{name}.mp4"))
    session = mc.sync_session(mc.new_session(folder=str(folder), people=["Nika", "Ana"]))
    assert not any(s.members for s in session.sources) and mc.resolved_style(session) == "studio"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_each_persons_video_and_audio_sync_together_and_start_with_the_rest(sandbox):
    folder = sandbox / "tracks"
    folder.mkdir()
    host, guest = _voices()
    for name, voice, color in (("nika", host, "red"), ("ana", guest, "blue")):
        _write_wav(folder / f"{name}_audio.wav", voice)
        _write_wav(sandbox / f"{name}.wav", voice * 0.5)
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30:d=40", "-i", str(sandbox / f"{name}.wav"),
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / f"{name}_video.mp4"))
    session = mc.new_session(folder=str(folder), people=["Nika", "Ana"])
    mc.update_mapping(session, {"sources": [
        {"id": s.id, "role": "camera" if s.kind == "video" else "mic", "person": "nika" if "nika" in os.path.basename(s.path) else "ana"}
        for s in session.sources
    ]})
    session = mc.sync_session(session)
    assert not mc.needs_sync(session), [(os.path.basename(s.path), s.role, s.offset, s.sync) for s in session.sources]
    assert "assumed" in {s.sync.get("status") for s in session.sources}
    assert any(s.members for s in session.sources)
    # Reopening the folder finds this edit, split screen and all.
    assert mc.new_session(folder=str(folder)).session_id == session.session_id


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_gallery_with_two_tiles_on_top_and_one_below(sandbox, tmp_path):
    video = tmp_path / "three.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=300x168:r=30:d=6", "-f", "lavfi", "-i", "mandelbrot=s=300x168:r=30",
            "-f", "lavfi", "-i", "life=s=300x168:r=30:mold=10:ratio=0.2",
            "-filter_complex",
            "color=c=0x101010:s=640x360:d=6[bg];[bg][0:v]overlay=10:8[a];[a][1:v]overlay=330:8[b];[b][2:v]overlay=170:184[v]",
            "-map", "[v]", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video))
    panes = mc.detect_panes(mc.probe_source(str(video)))
    assert len(panes) == 3
    top, bottom = sorted(panes, key=lambda p: (p[1], p[0]))[:2], sorted(panes, key=lambda p: (p[1], p[0]))[2]
    assert all(p[1] < 0.1 for p in top) and bottom[1] > 0.45 and 0.2 < bottom[0] < 0.3


def test_render_refuses_mapping_changes_that_would_drop_the_cut(sandbox, monkeypatch):
    import main as backend

    session = _bare(sandbox)
    session.cuts = [{"start": 0, "end": 30, "source_id": "a"}]
    session.save()
    sent = []
    monkeypatch.setattr(backend, "emit_result", lambda task, status, data=None, error=None: sent.append((status, error)))
    backend.handle_manage_multicam("t", {"action": "render", "session_id": session.session_id, "range_start": 5})
    assert sent[0][0] == "error" and "only look" in sent[0][1]
    assert mc.MulticamSession.load(session.session_id).cuts


def test_cli_json_always_prints_one_object(episode, monkeypatch, capsys, tmp_path):
    assert run_cli(monkeypatch, "--json") == 2
    assert "error" in json.loads(capsys.readouterr().out)
    cuts = tmp_path / "cuts.json"
    cuts.write_text(json.dumps([{"end": 3, "source_id": "x"}]))
    assert run_cli(monkeypatch, str(episode), "--cuts", str(cuts), "--no-render", "--json", "-y") == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert run_cli(monkeypatch, str(episode), "--delete", "--json") == 0
    assert json.loads(capsys.readouterr().out)["deleted"] is True


# --- review timelines and importing an edited timeline ---------------------------

def _two_camera_session(sandbox):
    a = _source(str(sandbox / "cam_a.mp4"), role="camera", person="nika", offset=0.0)
    b = _source(str(sandbox / "cam_b.mp4"), role="camera", person="ana", offset=1.0)
    m = _source(str(sandbox / "room.wav"), kind="audio", role="mic", offset=0.0)
    session = mc.MulticamSession(session_id="abc123abc777", name="ep", sources=[a, b, m], reference_id="room",
                                 cuts=[{"start": 2.0, "end": 10.0, "source_id": "cam_a"},
                                       {"start": 10.0, "end": 20.0, "source_id": "cam_b"},
                                       {"start": 20.0, "end": 30.0, "source_id": "cam_a"}])
    session.save()
    return mc.set_removals(session, [{"start": 12.0, "end": 14.0, "reason": "retake"}])


def _ripple_delete(xml_path, index):
    """Delete one shot from the picture track and close the gap, as an editor would."""
    tree = ET.parse(xml_path)
    track = tree.getroot().findall("./sequence/media/video/track")[-1]
    items = track.findall("clipitem")
    gone = items[index]
    length = int(gone.findtext("end")) - int(gone.findtext("start"))
    track.remove(gone)
    for item in items[index + 1:]:
        for tag in ("start", "end"):
            item.find(tag).text = str(int(item.findtext(tag)) - length)
    tree.write(xml_path)


def test_review_plan_keeps_removals_in_place_and_tagged(sandbox):
    session = _two_camera_session(sandbox)
    plan = mc.render_plan(session, 30.0, review=True)
    assert sum(p.frames for p in plan) == 28 * 30
    removed = [p for p in plan if p.removal]
    assert [(p.source_id, p.out_frame, p.frames) for p in removed] == [("cam_b", 300, 60)]
    assert removed[0].removal["reason"] == "retake"
    assert sum(p.frames for p in mc.render_plan(session, 30.0)) == 26 * 30


def test_review_export_stacks_every_camera_and_marks_removals(sandbox):
    session = _two_camera_session(sandbox)
    premiere = ET.parse(mc.export_xml(session, "premiere", review=True)).getroot()
    tracks = premiere.findall("./sequence/media/video/track")
    assert len(tracks) == 3
    assert int(premiere.findtext("./sequence/duration")) == 28 * 30
    flagged = [c for c in premiere.iter("clipitem") if c.findtext("labels/label2") == "Mango"]
    assert len(flagged) == 4
    assert all(c.findtext("name").startswith("Remove (retake): ") for c in flagged)
    marker = premiere.find("./sequence/marker")
    assert (marker.findtext("name"), marker.findtext("in"), marker.findtext("out")) == ("retake", "300", "360")
    assert session.outputs["premiere_review"].endswith("episode-review-premiere.xml")

    fcp = ET.parse(mc.export_xml(session, "fcpxml", review=True)).getroot()
    assert {c.get("lane") for c in fcp.iter("asset-clip")} == {"1", "2", "3", "-1"}
    assert [m.get("value") for m in fcp.iter("marker")] == ["retake"]

    plain = ET.parse(mc.export_xml(session, "premiere")).getroot()
    assert len(plain.findall("./sequence/media/video/track")) == 1
    assert plain.find("./sequence/marker") is None


def test_an_untouched_timeline_imports_as_the_same_edit(sandbox):
    session = _two_camera_session(sandbox)
    cuts, removals = [dict(c) for c in session.cuts], [dict(r) for r in session.removals]
    got = mc.import_timeline(session, mc.export_xml(session, "premiere"))
    assert got == {"shots": 3, "removals": 1, "skipped_clips": 0}
    assert [c["source_id"] for c in session.cuts] == [c["source_id"] for c in cuts]
    for new, old in zip(session.cuts, cuts):
        assert new["start"] == pytest.approx(old["start"], abs=0.04)
        assert new["end"] == pytest.approx(old["end"], abs=0.04)
    assert session.removals[0]["start"] == pytest.approx(removals[0]["start"], abs=0.04)
    assert session.removals[0]["end"] == pytest.approx(removals[0]["end"], abs=0.04)
    assert session.removals[0]["reason"] == "retake"


def test_keeping_a_marked_stretch_in_review_restores_it(sandbox):
    session = _two_camera_session(sandbox)
    mc.import_timeline(session, mc.export_xml(session, "premiere", review=True))
    assert session.removals == []
    assert session.cuts[0]["start"] == pytest.approx(2.0, abs=0.04)
    assert session.cuts[-1]["end"] == pytest.approx(30.0, abs=0.04)


def test_deleting_a_shot_in_the_editor_becomes_a_removal(sandbox):
    session = _two_camera_session(sandbox)
    mc.set_removals(session, [])
    path = mc.export_xml(session, "premiere")
    _ripple_delete(path, 1)
    got = mc.import_timeline(session, path)
    assert got["removals"] == 1
    assert session.removals[0]["start"] == pytest.approx(10.0, abs=0.04)
    assert session.removals[0]["end"] == pytest.approx(20.0, abs=0.04)
    assert [c["source_id"] for c in session.cuts] == ["cam_a"]
    assert session.cuts[0]["start"] == pytest.approx(2.0, abs=0.04)
    assert session.cuts[0]["end"] == pytest.approx(30.0, abs=0.04)


def test_import_refuses_reordered_or_foreign_timelines(sandbox):
    session = _two_camera_session(sandbox)
    path = mc.export_xml(session, "premiere")
    tree = ET.parse(path)
    items = tree.getroot().findall("./sequence/media/video/track")[-1].findall("clipitem")
    first, last = items[0], items[-1]
    length = int(last.findtext("out")) - int(last.findtext("in"))
    last.find("in").text = first.findtext("in")
    last.find("out").text = str(int(first.findtext("in")) + length)
    tree.write(path)
    before = [dict(c) for c in session.cuts]
    with pytest.raises(ValueError, match="out of order"):
        mc.import_timeline(session, path)
    assert session.cuts == before

    notes = sandbox / "notes.txt"
    notes.write_text("hi")
    with pytest.raises(ValueError, match="FCP 7 XML"):
        mc.import_timeline(session, str(notes))
    other = sandbox / "other.xml"
    other.write_text('<?xml version="1.0"?><fcpxml version="1.10"/>')
    with pytest.raises(ValueError, match="isn't an FCP 7 XML timeline"):
        mc.import_timeline(session, str(other))


def test_a_removal_where_cameras_stop_and_start_is_bridged_by_another_camera(sandbox):
    a = _source(str(sandbox / "cam_a.mp4"), role="camera", person="nika", offset=0.0, duration=11.0)
    b = _source(str(sandbox / "cam_b.mp4"), role="camera", person="ana", offset=13.0, duration=50.0)
    w = _source(str(sandbox / "cam_w.mp4"), role="camera", person="wide", offset=0.0)
    m = _source(str(sandbox / "room.wav"), kind="audio", role="mic", offset=0.0)
    session = mc.MulticamSession(session_id="abc123abc778", name="ep", sources=[a, b, w, m], reference_id="room",
                                 cuts=[{"start": 2.0, "end": 10.0, "source_id": "cam_a"},
                                       {"start": 10.0, "end": 30.0, "source_id": "cam_w"}])
    session.save()
    mc.set_cuts(session, [{"start": 2.0, "end": 10.0, "source_id": "cam_a"},
                          {"start": 10.0, "end": 16.0, "source_id": "cam_w"},
                          {"start": 16.0, "end": 30.0, "source_id": "cam_b"}])
    mc.set_removals(session, [{"start": 10.0, "end": 16.0}])
    mc.import_timeline(session, mc.export_xml(session, "premiere"))
    assert [c["source_id"] for c in session.cuts] == ["cam_a", "cam_w", "cam_b"]
    assert session.cuts[0]["end"] == pytest.approx(11.0, abs=0.04)
    assert session.cuts[1]["end"] == pytest.approx(13.0, abs=0.04)
    assert session.removals[0]["start"] == pytest.approx(10.0, abs=0.04)
    assert session.removals[0]["end"] == pytest.approx(16.0, abs=0.04)


@pytest.mark.parametrize(
    "fps,tc,expected",
    [
        # 25 fps: integer rate, plain frames/fps.
        (25.0, "01:00:00:10", 3600 + 10 / 25),
        # 29.97 non-drop frame: separator is ':', frame count is not adjusted,
        # but each frame is 1001/30000 s rather than 1/30 s.
        (29.97, "00:01:00:00", (30 * 60) * (1001 / 30000)),
        # 29.97 drop frame: separator is ';' before the frame field. At 1 minute
        # in, drop-frame counting has skipped 2 frame numbers versus wall time,
        # so :00;00 lands earlier than non-drop ':00:00:00' would.
        (29.97, "00:01:00;00", (30 * 60 - 2) * (1001 / 30000)),
        # 29.97 drop frame at the tenth minute: drop frame skips 2 counts at
        # the start of every minute except every tenth, so by minute 10 the
        # cumulative skip is 2 * (10 - 1) = 18 frames, not 0.
        (29.97, "00:10:00;00", (30 * 600 - 18) * (1001 / 30000)),
        # 59.94 drop frame: 4 frames skipped per non-tenth minute.
        (59.94, "00:01:00;00", (60 * 60 - 4) * (1001 / 60000)),
        # 23.976 non-drop: nominal 24 fps grid, real frame duration 1001/24000 s.
        (23.976, "00:00:10:00", (24 * 10) * (1001 / 24000)),
    ],
)
def test_timecode_seconds_handles_ntsc_pulldown_and_drop_frame(fps, tc, expected):
    info = {"format": {"tags": {"timecode": tc}}}
    assert mc._timecode_seconds(info, fps, 48000) == pytest.approx(expected, abs=1e-6)


def test_timecode_seconds_accepts_a_dot_separator_as_non_drop():
    # Some cameras and field recorders write the non-drop separator as '.'
    # instead of ':'. It must parse the same as the all-':' form, not read 0.0.
    info_dot = {"format": {"tags": {"timecode": "01.00.00.10"}}}
    info_colon = {"format": {"tags": {"timecode": "01:00:00:10"}}}
    assert mc._timecode_seconds(info_dot, 25.0, 48000) == pytest.approx(3600 + 10 / 25, abs=1e-6)
    assert mc._timecode_seconds(info_dot, 25.0, 48000) == mc._timecode_seconds(info_colon, 25.0, 48000)


def test_timecode_seconds_falls_back_to_time_reference_without_embedded_timecode():
    info = {"format": {"tags": {"time_reference": "48000"}}}
    assert mc._timecode_seconds(info, 29.97, 48000) == pytest.approx(1.0, abs=1e-6)


def _fake_probe(tmp_path, monkeypatch, video_stream):
    path = tmp_path / "cam.mov"
    path.write_bytes(b"0")
    info = {"format": {"duration": "10.0", "tags": {}}, "streams": [{"codec_type": "video", **video_stream}]}
    monkeypatch.setattr(mc, "get_video_info", lambda p: info)
    return mc.probe_source(str(path))


def test_timecode_pulldown_is_decided_from_r_frame_rate_not_the_noisy_average(tmp_path, monkeypatch):
    """A steady 25 fps camera's avg_frame_rate can measure 24.98 by noise alone.

    Deciding pulldown from that average instead of the stream's exact
    r_frame_rate would misread a plain 25 fps file as NTSC and shift its
    embedded start timecode by seconds.
    """
    src = _fake_probe(tmp_path, monkeypatch, {
        "avg_frame_rate": "2498/100", "r_frame_rate": "25/1",
        "tags": {"timecode": "01:00:00:10"},
    })
    assert src.timecode == pytest.approx(3600 + 10 / 25, abs=1e-6)


def test_probe_source_counts_multiple_audio_streams(tmp_path, monkeypatch):
    path = tmp_path / "cam.mxf"
    path.write_bytes(b"0")
    info = {"format": {"duration": "10.0", "tags": {}}, "streams": [
        {"codec_type": "video", "avg_frame_rate": "30/1", "r_frame_rate": "30/1"},
        {"codec_type": "audio", "channels": 1},
        {"codec_type": "audio", "channels": 1},
    ]}
    monkeypatch.setattr(mc, "get_video_info", lambda p: info)
    src = mc.probe_source(str(path))
    assert src.audio_stream_count == 2
    assert src.audio_stream_channels == [1, 1]
    assert src.audio_stream_index == 0


def test_update_mapping_validates_audio_stream_index(sandbox):
    session = mc.MulticamSession(
        session_id="abc123abc999", name="ep",
        people=[mc.Person("nika", "Nika")],
        sources=[mc.Source(id="a", path="/x/cam_a.mov", kind="video", duration=9, has_audio=True,
                            audio_channels=1, audio_stream_count=2, audio_stream_channels=[1, 2])],
    )
    with pytest.raises(ValueError, match="audio_stream_index"):
        mc.update_mapping(session, {"sources": [{"id": "a", "audio_stream_index": 5}]})
    session = mc.update_mapping(session, {"sources": [{"id": "a", "audio_stream_index": 1}]})
    src = session.source("a")
    assert src.audio_stream_index == 1
    assert src.audio_channels == 2  # recomputed from the selected stream, not the first one


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_extract_reads_the_selected_audio_stream_not_always_the_first(sandbox):
    path = sandbox / "two_streams.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=s=160x90:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path))
    src = mc.probe_source(str(path))
    assert src.audio_stream_count == 2

    silent = mc._read_wav(mc._extract(src, sandbox / "stream0.wav"))
    assert np.abs(silent).mean() < 50  # stream 0 is anullsrc: near silence

    src.audio_stream_index = 1
    tone = mc._read_wav(mc._extract(src, sandbox / "stream1.wav"))
    assert np.abs(tone).mean() > 1000  # stream 1 is a 440 Hz tone


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_mix_and_stems_honor_audio_stream_index_not_always_the_first_stream(sandbox):
    path = sandbox / "two_streams.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=s=160x90:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path))
    mic = _source(str(path), kind="video", role="mic", person="host", offset=0.0, duration=2.0,
                  audio_stream_count=2, audio_stream_channels=[1, 1], audio_stream_index=1)

    mix = sandbox / "mix.wav"
    mc._write_mix(mc.MulticamSession(session_id="abc123abc903", name="ep", sources=[mic]),
                  mix, 0.0, 2.0, encode=("-ac", "1", "-c:a", "pcm_s16le"), feeds=[(mic, -1)])
    assert np.abs(mc._read_wav(mix)).mean() > 1000  # the tone on stream 1, not the silence on stream 0

    session = mc.MulticamSession(session_id="abc123abc904", name="ep", people=[mc.Person("host", "Host")],
                                 sources=[mic])
    stems = mc._render_stems(session, sandbox, 0.0, 2.0)
    assert len(stems) == 1
    assert np.abs(_read_f32(stems[0])).mean() > 0.05


def test_probe_source_warns_on_variable_frame_rate(tmp_path, monkeypatch):
    # avg_frame_rate (what actually played) is far below r_frame_rate (the
    # stream's time base): frames held variable lengths.
    src = _fake_probe(tmp_path, monkeypatch, {"avg_frame_rate": "24/1", "r_frame_rate": "60/1"})
    assert src.fps == pytest.approx(24.0)
    assert "variable frame rate" in src.fps_warning.lower()


def test_probe_source_does_not_warn_on_a_steady_frame_rate(tmp_path, monkeypatch):
    src = _fake_probe(tmp_path, monkeypatch, {"avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001"})
    assert src.fps_warning == ""


def test_probe_source_warns_when_falling_back_to_the_default_frame_rate(tmp_path, monkeypatch):
    src = _fake_probe(tmp_path, monkeypatch, {"avg_frame_rate": "0/0", "r_frame_rate": "0/0"})
    assert src.fps == 30.0
    assert "assuming 30 fps" in src.fps_warning.lower()


# --- drift inside a shot ----------------------------------------------------------

def _drifting_session(sandbox, speed=1.0001):
    cam = _source(str(sandbox / "cam.mp4"), role="camera", person="host", offset=0.0, speed=speed, duration=700.0)
    mic = _source(str(sandbox / "mic.wav"), kind="audio", role="mic", person="host", offset=0.0, speed=speed,
                  duration=700.0)
    session = mc.MulticamSession(session_id="abc123abc888", name="ep", people=[mc.Person("host", "Host")],
                                 sources=[cam, mic], reference_id="mic",
                                 cuts=[{"start": 10.0, "end": 610.0, "source_id": "cam"}])
    session.save()
    return session


def test_a_long_shot_on_a_drifting_camera_stays_within_half_a_frame(sandbox):
    session = _drifting_session(sandbox)
    cam = session.source("cam")
    fps = 30.0
    plan = mc.render_plan(session, fps)
    assert len(plan) > 1
    assert session.cuts == [{"start": 10.0, "end": 610.0, "source_id": "cam"}]
    visible = mc._merged([{"start": p.tl_start, "end": p.tl_start + p.frames / fps, "source_id": p.source_id}
                          for p in plan])
    assert [(round(c["start"], 3), round(c["end"], 3), c["source_id"]) for c in visible] == [(10.0, 610.0, "cam")]
    assert sum(p.frames for p in plan) == 600 * 30

    worst = 0.0
    for p in plan:
        start = cam.source_in(p.tl_start, p.frames / fps)
        # Played at real speed, the slip is linear across a piece, so its ends are the worst frames.
        for k in (0, p.frames):
            worst = max(worst, abs(start + k / fps - cam.source_time(p.tl_start + k / fps)))
    assert worst < 0.5 / fps
    # Mapping the whole shot at its start would have slipped 60 ms by the end.
    assert abs(cam.source_time(10.0) + 600.0 - cam.source_time(610.0)) > 0.05


def test_drift_split_reaches_the_render_seek_and_both_editor_timelines(sandbox):
    session = _drifting_session(sandbox)
    cam = session.source("cam")
    fps = 30.0
    plan = mc.render_plan(session, fps)

    piece = plan[1]
    command = mc._shot_command(session, cam, piece.tl_start, piece.frames, 320, 180, fps, "none")
    seek = float(command[command.index("-ss") + 1])
    assert seek == pytest.approx(cam.source_in(piece.tl_start, piece.frames / fps), abs=1e-4)

    premiere = ET.parse(mc.export_xml(session, "premiere")).getroot()
    assert len(premiere.findall("./sequence/media/video/track/clipitem")) == len(plan)
    assert len(premiere.findall("./sequence/media/audio/track/clipitem")) >= len(plan)

    fcp = ET.parse(mc.export_xml(session, "fcpxml")).getroot()
    mics = [c for c in fcp.iter("asset-clip") if c.get("lane") == "-1"]
    assert len(mics) >= len(plan)
    for clip in mics:
        offset, start, duration = (float(Fraction(clip.get(k).rstrip("s"))) for k in ("offset", "start", "duration"))
        middle = 10.0 + offset + duration / 2
        # Each mic piece is pinned at its middle; a sample of rounding is all that's left there.
        assert abs(start + duration / 2 - session.source("mic").source_time(middle)) < 1e-3


# --- render validation and the shot cache ------------------------------------------

def _planned(folder):
    session = mc.new_session(folder=str(folder), people=["Nika", "Ana"])
    mc.update_mapping(session, {"sources": [
        {"id": s.id, "role": "camera", "person": "nika" if "one" in os.path.basename(s.path) else "ana"}
        for s in session.sources if s.kind == "video"
    ]})
    return mc.plan_session(mc.sync_session(session))


def _counting_encodes(monkeypatch):
    real = mc.proc_run
    encodes = []

    def run(cmd, **kw):
        if "-frames:v" in cmd and "-filter_complex" in cmd or "lavfi" in cmd and "-frames:v" in cmd:
            encodes.append(cmd)
        return real(cmd, **kw)

    monkeypatch.setattr(mc, "proc_run", run)
    return encodes


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_reports_validation_and_reuses_cached_shots(episode, monkeypatch):
    session = _planned(episode)
    encodes = _counting_encodes(monkeypatch)
    outputs = mc.render_session(session)
    shots = len(mc.render_plan(session, 30.0))
    assert len(encodes) == shots
    v = outputs["validation"]
    assert v["frames_expected"] == v["frames_actual"] == sum(p.frames for p in mc.render_plan(session, 30.0))
    assert v["duration"] == pytest.approx(v["frames_expected"] / 30.0, abs=0.05)
    assert v["lufs"] is not None and v["true_peak"] is not None
    assert isinstance(v["warnings"], list)
    assert mc.MulticamSession.load(session.session_id).outputs["validation"] == v

    # Dropping the stems changes the render but not one shot's pixels: nothing re-encodes.
    encodes.clear()
    mc.render_session(session, stems=False)
    assert encodes == []
    # A removal re-encodes only the pieces it splits.
    mc.set_removals(session, [{"start": 5.0, "end": 6.0}])
    mc.render_session(session)
    assert 0 < len(encodes) < len(mc.render_plan(session, 30.0))
    encodes.clear()

    # A cached shot that lost frames is caught by its count and encoded again.
    cached = sorted((mc._work_dir(session.session_id) / "shots").glob("*/shot.mp4"))
    cached[0].write_bytes(cached[0].read_bytes()[: cached[0].stat().st_size // 3])
    session.outputs.pop("render_key")
    again = mc.render_session(session)["validation"]
    assert again["frames_actual"] == again["frames_expected"] == v["frames_expected"] - 30
    assert len(encodes) == 1


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_warns_when_a_camera_runs_out_or_the_mix_is_short(sandbox, monkeypatch):
    folder = sandbox / "short"
    folder.mkdir()
    voice = _speech(40, 9)
    _write_wav(folder / "rec_Tr1.wav", voice)
    _write_wav(sandbox / "cam.wav", voice * 0.6)
    # The picture stops 2 s before the camera's own sound does.
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=38", "-i", str(sandbox / "cam.wav"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / "cam_one.mp4"))
    session = mc.sync_session(mc.new_session(folder=str(folder), people=["Nika"]))
    cam = next(s for s in session.sources if s.kind == "video")
    mc.update_mapping(session, {"sources": [{"id": cam.id, "role": "camera", "person": "nika"}]})
    mc.set_cuts(session, [{"start": 1.0, "end": cam.timeline_end() - 0.1, "source_id": cam.id}])

    real_audio = mc._render_audio

    def short_audio(session, out, start, duration, splice=None):
        return real_audio(session, out, start, duration - 1.0, splice)

    monkeypatch.setattr(mc, "_render_audio", short_audio)
    outputs = mc.render_session(session, stems=False)
    v = outputs["validation"]
    assert any("cam_one.mp4 ran out" in w for w in v["warnings"])
    assert any("shorter than the picture" in w for w in v["warnings"])
    # The short mix no longer trims the picture to fit.
    assert v["frames_actual"] == v["frames_expected"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_fails_on_decode_errors_or_a_wrong_frame_count(episode, monkeypatch):
    session = _planned(episode)
    out_dir = mc._output_dir(session)
    monkeypatch.setattr(mc, "_decode_errors", lambda path, **k: "Invalid NAL unit size")
    with pytest.raises(RuntimeError, match="decode"):
        mc.render_session(session)
    assert not (out_dir / "episode.mp4").exists()
    monkeypatch.undo()

    real = mc._frame_count
    monkeypatch.setattr(mc, "_frame_count", lambda p: real(p) - (2 if p.name == "episode.mp4" else 0))
    with pytest.raises(RuntimeError, match="frames"):
        mc.render_session(session)
    assert not (out_dir / "episode.mp4").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_decode_check_samples_a_few_short_spans_by_default_not_the_whole_file(episode, monkeypatch):
    """A full decode of a finished episode costs minutes per hour of 1080p.

    The default check must still pass a clean file, but it must do it by
    decoding a handful of short, bounded spans, not the whole thing; asking
    for validate="full" is the only way to get the exhaustive decode.
    """
    video = mc.render_session(_planned(episode), stems=False)["video"]
    calls = []
    real_run = mc.proc_run

    def recording_run(cmd, **k):
        calls.append(cmd)
        return real_run(cmd, **k)

    monkeypatch.setattr(mc, "proc_run", recording_run)
    assert mc._decode_errors(video) == ""
    decodes = [c for c in calls if c[0] == "ffmpeg"]
    assert decodes and all("-t" in c for c in decodes), "every sampled decode must be bounded to a short span"
    assert len(decodes) <= 5

    calls.clear()
    assert mc._decode_errors(video, full=True) == ""
    decodes = [c for c in calls if c[0] == "ffmpeg"]
    assert len(decodes) == 1 and "-t" not in decodes[0], "a full decode has no time bound"


# --- audio chain --------------------------------------------------------------------

def _read_f32(path, channels=1):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", str(channels), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_drift_correction_resamples_to_the_exact_length_without_audible_pitch_shift(sandbox):
    speed = 1.0 + 100e-6
    t = np.arange(60 * RATE) / RATE
    _write_wav(sandbox / "tone.wav", np.sin(2 * np.pi * 1000 * t) * 0.6)
    tone = _source(str(sandbox / "tone.wav"), kind="audio", role="mic", offset=0.5, speed=speed)
    session = mc.MulticamSession(session_id="abc123abc901", name="ep", sources=[tone])
    out = sandbox / "stretched.wav"
    mc._write_mix(session, out, 0.0, 62.0, encode=("-c:a", "pcm_f32le"), feeds=[(tone, -1)])
    y = _read_f32(out)
    sounding = np.flatnonzero(np.abs(y) > 1e-3)
    first, last = sounding[0] / RATE, (sounding[-1] + 1) / RATE
    assert first == pytest.approx(0.5, abs=1e-3)
    # 60 s of source lasts 60 * speed on the timeline: 6 ms longer, matched to under a millisecond.
    assert last - first == pytest.approx(60 * speed, abs=1e-3)
    seg = y[int(10 * RATE):int(50 * RATE)]
    spectrum = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 23))
    freq = np.argmax(spectrum) * RATE / (1 << 23)
    cents = 1200 * np.log2(freq / 1000)
    assert abs(cents) < 0.5


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_the_mix_high_passes_each_mic_before_leveling(sandbox):
    t = np.arange(20 * RATE) / RATE
    voice = _speech(20, 11)
    _write_wav(sandbox / "rumble.wav", 0.5 * voice + 0.4 * np.sin(2 * np.pi * 30 * t))
    mic = _source(str(sandbox / "rumble.wav"), kind="audio", role="mic", offset=0.0, duration=20.0)
    session = mc.MulticamSession(session_id="abc123abc902", name="ep", sources=[mic])
    assert mc.VOICE_CHAIN[0].startswith("highpass=f=70") and mc.VOICE_CHAIN[1].startswith("dynaudnorm")

    def hum_share(x):
        spectrum = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / RATE)
        return spectrum[(f > 25) & (f < 35)].sum() / spectrum[(f > 500) & (f < 4000)].sum()

    out = sandbox / "mixed.wav"
    mc._write_mix(session, out, 0.0, 20.0, per_feed=mc.VOICE_CHAIN, encode=("-c:a", "pcm_f32le"), feeds=[(mic, -1)])
    before = hum_share(_read_f32(sandbox / "rumble.wav"))
    after = hum_share(_read_f32(out))
    assert 10 * np.log10(before / after) > 10


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_two_pass_mix_hits_the_target_and_the_stems_sum_to_it(episode, monkeypatch):
    session = _planned(episode)
    work = mc._work_dir(session.session_id)
    start, end = session.cuts[0]["start"], session.cuts[-1]["end"]
    gain = mc._render_audio(session, work / "mix.wav", start, end - start)
    stems = mc._render_stems(session, work, start, end - start, None, gain)
    assert len(stems) == 2
    mix = _read_f32(work / "mix.wav")
    total = sum(_read_f32(p) for p in stems)
    assert len(total) == len(mix)
    # The limiter only touches peaks the gain pushed past the ceiling; everywhere else the stems add up exactly.
    calm = np.abs(total) < 10 ** ((mc.TRUE_PEAK_CEILING - 1.0) / 20)
    assert calm.mean() > 0.9
    assert np.abs(total[calm] - mix[calm]).max() < 2e-3

    lufs, _ = mc._loudness(work / "mix.wav")
    assert lufs == pytest.approx(mc.LOUDNESS_TARGET, abs=0.5)
    v = mc.render_session(session, stems=False)["validation"]
    assert v["lufs"] == pytest.approx(mc.LOUDNESS_TARGET, abs=1.0)
    assert v["true_peak"] <= mc.TRUE_PEAK_CEILING + 0.5
    assert v["warnings"] == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_loudness_gain_is_measured_on_the_kept_audio_not_the_removed_stretch(sandbox):
    """A removed stretch (a retake, a long silence) shouldn't pull the

    target gain around: it never airs. Measuring it on the whole premix
    instead of what the splice keeps asks for the wrong gain for the
    episode anyone actually hears.
    """
    kept = _speech(10, 21)
    # A removed stretch, loud enough to drag a whole-file measurement's
    # target gain down well below what the kept 10 s alone would ask for.
    loud_removed = 8.0 * _speech(5, 22)
    _write_wav(sandbox / "mic.wav", np.concatenate([kept, loud_removed]))
    mic = _source(str(sandbox / "mic.wav"), kind="audio", role="mic", offset=0.0, duration=15.0)
    session = mc.MulticamSession(session_id="abc123abc907", name="ep", sources=[mic])

    gain_whole = mc._render_audio(session, sandbox / "whole.wav", 0.0, 15.0)
    gain_kept = mc._render_audio(session, sandbox / "kept.wav", 0.0, 15.0, splice=[(0.0, 10.0)])
    assert gain_kept != pytest.approx(gain_whole, abs=0.5)

    lufs_kept, _ = mc._loudness(sandbox / "kept.wav")
    assert lufs_kept == pytest.approx(mc.LOUDNESS_TARGET, abs=0.5)
    assert mc._media_duration(sandbox / "kept.wav") == pytest.approx(10.0, abs=0.05)


# --- manual drift anchors -------------------------------------------------------------

def test_anchors_fit_offset_and_drift_by_least_squares(sandbox):
    session = _bare(sandbox)
    cam = session.sources[0]
    speed = 1.0 + 80e-6
    pairs = [{"timeline": 12.0 + speed * src + jitter, "source": src}
             for src, jitter in ((5.0, 0.0), (20.0, 0.002), (40.0, -0.002), (55.0, 0.0))]
    mc.update_mapping(session, {"sources": [{"id": cam.id, "anchors": pairs}]})
    assert cam.offset == pytest.approx(12.0, abs=0.003)
    assert cam.speed == pytest.approx(speed, abs=1e-4)
    assert cam.sync["status"] == "manual" and cam.sync["method"] == "manual"
    assert 0 < cam.sync["residual_ms"] < 3
    assert [p["source"] for p in cam.sync["anchors"]] == [5.0, 20.0, 40.0, 55.0]

    mc.update_mapping(session, {"sources": [{"id": cam.id, "anchors": [{"timeline": 30.0, "source": 10.0}]}]})
    assert (cam.offset, cam.speed) == (20.0, 1.0)
    assert cam.sync["residual_ms"] == 0


def test_anchors_are_refused_when_they_run_backwards_or_imply_impossible_drift(sandbox):
    session = _bare(sandbox)
    cam = session.sources[0]
    for bad in (
        [],
        [{"timeline": 10.0}],
        [{"timeline": 10.0, "source": 5.0}, {"timeline": 9.0, "source": 15.0}],
        [{"timeline": 10.0, "source": 5.0}, {"timeline": 11.0, "source": 5.0}],
        [{"timeline": 10.0, "source": 5.0}, {"timeline": 30.0, "source": 15.0}],
        [{"timeline": 10.0, "source": 500.0}],
    ):
        with pytest.raises(ValueError):
            mc.update_mapping(session, {"sources": [{"id": cam.id, "anchors": bad}]})
    assert cam.sync.get("method") != "manual"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_anchors_sync_a_camera_with_no_audio_and_survive_a_resync(sandbox):
    folder = sandbox / "mute"
    folder.mkdir()
    _write_wav(folder / "rec_Tr1.wav", _speech(40, 12))
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(folder / "cam_one.mp4"))
    session = mc.sync_session(mc.new_session(folder=str(folder), people=["Nika"]))
    cam = next(s for s in session.sources if s.kind == "video")
    assert not cam.has_audio and not cam.synced

    speed = 1.0 + 50e-6
    mc.update_mapping(session, {"sources": [{"id": cam.id, "role": "camera", "person": "nika", "anchors": [
        {"timeline": 4.0 + speed * 2.0, "source": 2.0}, {"timeline": 4.0 + speed * 28.0, "source": 28.0}]}]})
    assert cam.offset == pytest.approx(4.0, abs=1e-6) and cam.speed == pytest.approx(speed, abs=1e-9)

    session = mc.sync_session(session)
    cam = session.source(cam.id)
    assert cam.sync["method"] == "manual" and cam.speed == pytest.approx(speed, abs=1e-9)
    assert cam.offset == pytest.approx(4.0, abs=1e-6)
    session = mc.plan_session(session)
    assert {c["source_id"] for c in session.cuts} == {cam.id}
    assert mc.render_plan(session, 30.0)


# --- per-camera input LUTs ------------------------------------------------------------

def _cube(path, size=2, entries=None, header=None):
    lines = header if header is not None else ['TITLE "invert"', f"LUT_3D_SIZE {size}"]
    grid = [f"{1 - r / (size - 1):.4f} {1 - g / (size - 1):.4f} {1 - b / (size - 1):.4f}"
            for b in range(size) for g in range(size) for r in range(size)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines + grid[:entries]) + "\n", encoding="utf-8")
    return path


def test_input_lut_must_be_a_complete_3d_cube(sandbox):
    session = _bare(sandbox)
    cam = session.sources[0]
    good = _cube(sandbox / "luts" / "invert.cube")
    mc.update_mapping(session, {"sources": [{"id": cam.id, "input_lut": str(good)}]})
    assert cam.input_lut == str(good)
    for bad, match in (
        (str(_cube(sandbox / "luts" / "short.cube", entries=7)), "holds 7"),
        (str(_cube(sandbox / "luts" / "flat.cube", header=["LUT_1D_SIZE 2"])), "1D"),
        (str(_cube(sandbox / "luts" / "nosize.cube", header=[])), "LUT_3D_SIZE"),
        ("luts/invert.cube", "absolute"),
        (str(sandbox / "luts" / "missing.cube"), "isn't a .cube"),
    ):
        with pytest.raises(ValueError, match=match):
            mc.update_mapping(session, {"sources": [{"id": cam.id, "input_lut": bad}]})
    mc.update_mapping(session, {"sources": [{"id": cam.id, "input_lut": ""}]})
    assert cam.input_lut == ""


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_input_lut_colors_renders_stills_and_looks_and_exports_hand_it_off(episode):
    session = _planned(episode)
    red = next(s for s in session.sources if s.path.endswith("cam_one.mp4"))
    # Spaces, a colon and brackets in the path exercise the filter graph escaping.
    # Windows forbids a colon in a folder name, but its drive letter puts one
    # in every path there anyway.
    folder = "grade [v1], final" if os.name == "nt" else "grade: [v1], final"
    lut = _cube(episode.parent / folder / "invert.cube")
    before = mc.previews(session)["cameras"][red.id]
    mc.update_mapping(session, {"sources": [{"id": red.id, "input_lut": str(lut)}]})
    assert session.cuts, "a LUT is color, so the cut stays"

    stills = mc.previews(session, looks=True)
    assert stills["cameras"][red.id] != before
    inverted = _mean_rgb(stills["cameras"][red.id], 0)
    assert inverted[0] < 80 and inverted[1] > 150 and inverted[2] > 150
    assert set(stills["looks"]) == {c.id for c in session.cameras()}
    assert all(set(per) == set(mc.LOOKS) for per in stills["looks"].values())

    video = mc.render_session(session, stems=False)["video"]
    on_red = next(c for c in session.cuts if c["source_id"] == red.id)
    pixel = _mean_rgb(video, (on_red["start"] + on_red["end"]) / 2 - session.cuts[0]["start"])
    assert pixel[0] < 80 and pixel[2] > 150

    mc.export_xml(session, "fcpxml")
    handoff = json.loads(open(session.outputs["color_handoff"], encoding="utf-8").read())
    assert os.path.dirname(session.outputs["color_handoff"]) == os.path.dirname(session.outputs["fcpxml"])
    luts = {c["source_id"]: c["input_lut"] for c in handoff["cameras"]}
    assert luts[red.id] == str(lut)
    assert all(v is None for k, v in luts.items() if k != red.id)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_previews_only_renders_looks_for_cameras_actually_used_in_the_cut(episode):
    """Four look stills per camera is wasted work for a camera that's mapped

    but never actually appears in the cut (an alternate angle left in,
    say). Once a cut exists, only cameras it actually uses should get them.
    """
    session = _planned(episode)
    cam_one = next(s for s in session.sources if s.path.endswith("cam_one.mp4"))
    cam_two = next(s for s in session.sources if s.path.endswith("cam_two.mp4"))
    mc.set_cuts(session, [{"start": session.cuts[0]["start"], "end": session.cuts[-1]["end"],
                           "source_id": cam_one.id}])

    stills = mc.previews(session, looks=True)
    assert cam_one.id in stills["looks"]
    assert cam_two.id not in stills["looks"]


def test_a_lut_deleted_after_mapping_fails_fast_and_names_the_camera(episode):
    """A LUT picked at map time can later be moved or deleted on disk.

    Without an upfront check, ffmpeg's own error about the missing file
    surfaces from deep inside a shot or a still render, with nothing saying
    which camera's LUT broke.
    """
    session = _planned(episode)
    red = next(s for s in session.sources if s.path.endswith("cam_one.mp4"))
    lut = _cube(episode.parent / "luts" / "invert.cube")
    mc.update_mapping(session, {"sources": [{"id": red.id, "input_lut": str(lut)}]})

    os.remove(lut)

    with pytest.raises(ValueError, match="cam_one.mp4") as render_exc:
        mc.render_session(session, stems=False)
    assert "missing" in str(render_exc.value).lower()

    with pytest.raises(ValueError, match="cam_one.mp4") as preview_exc:
        mc.previews(session)
    assert "missing" in str(preview_exc.value).lower()


def test_lut_is_refused_on_a_mic(sandbox):
    session = _bare(sandbox)
    session.sources.append(_source("/x/room.wav", kind="audio", role="mic", id="room"))
    with pytest.raises(ValueError, match="camera"):
        mc.update_mapping(session, {"sources": [{"id": "room", "input_lut": str(_cube(sandbox / "l" / "a.cube"))}]})


# --- fitting cameras of another size into the sequence --------------------------------

def test_exports_fit_cameras_of_another_size_inside_the_sequence(sandbox):
    wide = _source(str(sandbox / "wide.mp4"), role="camera", person="wide", offset=0.0, id="wide")
    uhd = _source(str(sandbox / "uhd.mp4"), role="camera", person="host", offset=0.0, id="uhd", width=3840, height=2160)
    phone = _source(str(sandbox / "phone.mp4"), role="camera", person="guest", offset=0.0, id="phone",
                    width=1080, height=1440)
    room = _source(str(sandbox / "room.wav"), kind="audio", role="mic", offset=0.0, id="room")
    session = mc.MulticamSession(
        session_id="abc123abc903", name="ep", people=[mc.Person("host", "Host"), mc.Person("guest", "Guest")],
        sources=[wide, uhd, phone, room], reference_id="room",
        cuts=[{"start": 0.0, "end": 5.0, "source_id": "wide"}, {"start": 5.0, "end": 10.0, "source_id": "uhd"},
              {"start": 10.0, "end": 15.0, "source_id": "phone"}])
    session.save()

    premiere = ET.parse(mc.export_xml(session, "premiere", review=True)).getroot()
    assert premiere.findtext("./sequence/media/video/format/samplecharacteristics/width") == "1920"
    scales: dict = {}
    for item in premiere.iter("clipitem"):
        effect = item.find("filter/effect")
        if item.find("sourcetrack") is not None:
            assert effect is None
            continue
        name = item.findtext("name")
        if effect is None:
            scales.setdefault(name, set()).add(None)
            continue
        assert [effect.findtext(k) for k in ("name", "effectid", "effectcategory", "effecttype", "mediatype")] == [
            "Basic Motion", "basic", "motion", "motion", "video"]
        param = effect.find("parameter")
        assert param.get("authoringApp") == "PremierePro"
        assert [param.findtext(k) for k in ("parameterid", "name", "valuemin", "valuemax")] == ["scale", "Scale", "0", "1000"]
        scales.setdefault(name, set()).add(float(param.findtext("value")))
    assert scales == {"wide.mp4": {None}, "uhd.mp4": {50.0}, "phone.mp4": {75.0}}

    fcp = ET.parse(mc.export_xml(session, "fcpxml", review=True)).getroot()
    for clip in fcp.iter("asset-clip"):
        fitted = [c.get("type") for c in clip.findall("adjust-conform")]
        expected = ["fit"] if clip.get("name") in ("uhd.mp4", "phone.mp4") else []
        assert fitted == expected, clip.get("name")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_a_fresh_edit_with_no_people_summarizes_before_its_first_sync(episode):
    session = mc.new_session(folder=str(episode))
    assert session.reference_id == ""
    data = mc.payload(session)
    assert data["preview"] is None
