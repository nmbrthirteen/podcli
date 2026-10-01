import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config.paths import paths  # noqa: E402
from services import multicam as mc  # noqa: E402
from services import multicam_cloud, podcli_cloud  # noqa: E402

import test_multicam  # noqa: E402

# Shared with the engine tests: a sandboxed home, and a two-camera episode.
sandbox = test_multicam.sandbox
episode = test_multicam.episode
run_cli = test_multicam.run_cli


class FakeCloud(BaseHTTPRequestHandler):
    """The four routes hybrid mode uses, and storage that keeps what was PUT."""

    state: dict = {}

    def log_message(self, *args):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return self.rfile.read(int(self.headers.get("content-length") or 0))

    def do_POST(self):
        s = self.state
        assert self.headers["authorization"] == "Bearer test-token"
        body = json.loads(self._body() or b"{}")
        if self.path == "/v1/multicam/hybrid":
            s["opened"] = body
            uploads = [{"name": f["name"], "storageKey": f"k/{f['name']}",
                        "uploadUrl": f"{s['base']}/store/{f['name'].replace(':', '_')}"} for f in body["files"]]
            return self._json(201, {"id": "11111111-2222-3333-4444-555555555555", "uploads": uploads})
        if self.path.endswith("/hybrid-complete"):
            s["completed"] = body
            return self._json(200, {"status": "ready"})
        self._json(404, {"error": "not found"})

    def do_PUT(self):
        self.state.setdefault("stored", {})[self.path] = len(self._body())
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path.endswith("/edit"):
            return self._json(200, self.state["edit"])
        self._json(404, {"error": "not found"})


@pytest.fixture
def cloud(sandbox, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloud)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    FakeCloud.state = {"base": base}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("PODCLI_API_URL", base)
    monkeypatch.setitem(paths, "home", str(sandbox / "home"))
    Path(paths["home"]).mkdir(parents=True, exist_ok=True)
    podcli_cloud.write_token("test-token")
    yield FakeCloud.state
    server.shutdown()


def test_editor_url_sits_beside_the_api(monkeypatch):
    monkeypatch.setenv("PODCLI_API_URL", "https://api.podcli.com")
    assert multicam_cloud.editor_url("abc") == "https://podcli.com/app/multicam/abc"
    monkeypatch.setenv("PODCLI_APP_URL", "http://localhost:5173/app")
    assert multicam_cloud.editor_url("abc") == "http://localhost:5173/app/multicam/abc"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_sends_only_previews_and_renders_the_cloud_cut_here(episode, cloud, monkeypatch, capsys):
    words = [{"start": 1.0, "end": 1.4, "text": "Hello", "person": "nika"}]
    monkeypatch.setattr(mc, "transcript", lambda *a, **k: {"words": words})
    code = run_cli(monkeypatch, str(episode), "--people", "Nika, Ana", "--set", "cam_one=camera:nika",
                   "--set", "cam_two=camera:ana", "--cloud", "-y")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "/app/multicam/11111111-2222-3333-4444-555555555555" in out

    sent = cloud["opened"]
    names = sorted(f["name"].split(":")[0] for f in sent["files"])
    assert names == ["audio", "proxy", "proxy", "still", "still", "transcript"]
    assert sent["engine"]["cuts"] and "preview" not in sent["engine"]
    assert set(sent["activity"]["people"]) == {"nika", "ana"}
    assert len(cloud["stored"]) == 6 and all(size > 0 for size in cloud["stored"].values())
    sizes = {f["name"]: f["sizeBytes"] for f in cloud["completed"]["files"]}
    camera_bytes = sum((episode / f"cam_{n}.mp4").stat().st_size for n in ("one", "two"))
    assert sizes["transcript"] < 1000 and sum(sizes.values()) < 4 * camera_bytes

    session = mc.MulticamSession.load(mc.list_sessions()[0]["session_id"])
    assert session.cloud["id"] == "11111111-2222-3333-4444-555555555555"
    assert not session.outputs.get("video")

    one = next(s.id for s in session.sources if s.path.endswith("cam_one.mp4"))
    end = sent["engine"]["cuts"][-1]["end"]
    start = sent["engine"]["cuts"][0]["start"]
    cloud["edit"] = {
        "state": {**sent["engine"], "people": [{"id": "nika", "name": "Nika"}, {"id": "ana", "name": "Ana Smith"}]},
        "cuts": [{"start": start, "end": end, "source_id": one}],
        "removals": [{"start": 10, "end": 12}],
        "look": "warm",
        "revision": 4,
    }
    code = run_cli(monkeypatch, "11111111-2222-3333-4444-555555555555", "--pull", "--no-stems")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Pulled the cloud edit: 1 shots" in out
    session = mc.MulticamSession.load(session.session_id)
    assert [p.name for p in session.people] == ["Nika", "Ana Smith"]
    assert session.look == "warm" and session.removals == [{"start": 10.0, "end": 12.0}]
    assert session.outputs["duration"] == pytest.approx(end - start - 2, abs=0.05)


def test_pull_needs_an_edit_that_was_sent(sandbox):
    with pytest.raises(multicam_cloud.MulticamCloudError, match="No multicam edit on this computer"):
        multicam_cloud.resolve("11111111-2222-3333-4444-555555555555")
