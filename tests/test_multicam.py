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


def test_shot_frames_fills_gaps_on_one_grid():
    session = mc.MulticamSession(session_id="abc123abc126", name="ep", cuts=[
        {"start": 10.0, "end": 12.51, "source_id": "a"},
        {"start": 12.51, "end": 14.0, "source_id": "b"},
        {"start": 15.0, "end": 16.0, "source_id": "a"},
    ])
    frames = mc.shot_frames(session, 30.0)
    assert frames == [("a", 0, 75), ("b", 75, 45), (None, 120, 30), ("a", 150, 30)]


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
    assert len(vclips) == len([f for f in mc.shot_frames(session, 30.0) if f[0]])

    fcp = ET.parse(mc.export_xml(session, "fcpxml")).getroot()
    lanes = {c.get("lane") for c in fcp.iter("asset-clip")}
    assert lanes == {"1", "-1", "-2"}

    # Rendering again with nothing changed reuses the file.
    before = os.path.getmtime(video)
    assert mc.render_session(session)["video"] == video
    assert os.path.getmtime(video) == before

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
