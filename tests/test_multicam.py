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
