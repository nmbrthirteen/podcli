"""Hybrid multicam: prepare and render here, steer the cut in the podcli cloud editor.

The camera files never leave this machine. Only what the browser editor plays
goes up: a small proxy per camera, the mic mix, a still per camera, the
transcript and the edit itself. About 550 MB for a two-hour, three-camera
episode, where the originals would be about 20 GB.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from services import multicam as mc
from services import podcli_cloud

ProgressCallback = Optional[Callable[[float, str], None]]

CONTENT_TYPES = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".jpg": "image/jpeg", ".json": "application/json"}


class MulticamCloudError(RuntimeError):
    pass


def editor_url(edit_id: str) -> str:
    """Where the edit opens in a browser: the app sits beside the API, at /app."""
    override = os.environ.get("PODCLI_APP_URL", "").rstrip("/")
    if override:
        return f"{override}/multicam/{edit_id}"
    api = urllib.parse.urlparse(podcli_cloud.api_url())
    host = api.netloc[4:] if api.netloc.startswith("api.") else api.netloc
    return f"{api.scheme}://{host}/app/multicam/{edit_id}"


def _emit(cb: ProgressCallback, pct: float, msg: str) -> None:
    if cb:
        cb(pct, msg)


def _put(url: str, path: str) -> int:
    size = os.path.getsize(path)
    ctype = CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    with open(path, "rb") as fh:
        req = urllib.request.Request(url, data=fh, method="PUT",
                                     headers={"content-type": ctype, "content-length": str(size)})
        try:
            with urllib.request.urlopen(req, timeout=3600):
                pass
        except urllib.error.HTTPError as exc:
            raise MulticamCloudError(f"upload of {os.path.basename(path)} failed: HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise MulticamCloudError(f"upload of {os.path.basename(path)} failed: {exc.reason}") from None
    return size


def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    try:
        return podcli_cloud.request(method, path, body, timeout=120) or {}
    except podcli_cloud.CloudError as exc:
        raise MulticamCloudError(str(exc)) from None


def push(session: mc.MulticamSession, *, model_size: str = "base", engine: Optional[str] = None,
         progress_callback: ProgressCallback = None) -> mc.MulticamSession:
    """Builds what the editor plays, sends it, and remembers which cloud edit this session became."""
    if not podcli_cloud.signed_in():
        raise MulticamCloudError("not signed in to podcli cloud. Run `podcli login` first")
    if not session.cuts:
        raise MulticamCloudError("plan the cut before sending the edit")

    _emit(progress_callback, 5, "Writing the transcript")
    words = mc.transcript(session, model_size=model_size, engine=engine)["words"]
    _emit(progress_callback, 35, "Making previews")
    session = mc.build_preview(session)
    activity = mc.activity(session)
    data = mc.payload(session)
    preview = data.pop("preview", None) or {}
    for key in ("outputs", "stats"):
        data.pop(key, None)
    if not preview.get("audio"):
        raise MulticamCloudError("the preview mix wasn't built")

    with tempfile.TemporaryDirectory(prefix="podcli_cloud_") as tmp:
        transcript = os.path.join(tmp, "transcript.json")
        with open(transcript, "w", encoding="utf-8") as f:
            json.dump({"words": words}, f)
        files = {"audio": preview["audio"], "transcript": transcript}
        files.update({f"proxy:{k}": v for k, v in (preview.get("proxies") or {}).items()})
        files.update({f"still:{k}": v for k, v in (preview.get("stills") or {}).items()})

        opened = _request("POST", "/v1/multicam/hybrid", {
            "name": session.name,
            "engine": data,
            "activity": activity,
            "files": [{"name": n, "sizeBytes": max(1, os.path.getsize(p))} for n, p in files.items()],
        })
        stored = []
        total = len(opened.get("uploads", [])) or 1
        for i, slot in enumerate(opened.get("uploads", [])):
            _emit(progress_callback, 60 + 35 * i / total, f"Uploading {slot['name'].split(':')[0]}")
            size = _put(slot["uploadUrl"], files[slot["name"]])
            stored.append({"name": slot["name"], "storageKey": slot["storageKey"], "sizeBytes": size})

    _request("POST", f"/v1/multicam/{opened['id']}/hybrid-complete", {"files": stored})
    latest = mc.MulticamSession.load(session.session_id)
    latest.cloud = {"id": opened["id"], "url": editor_url(opened["id"])}
    latest.save()
    _emit(progress_callback, 100, "In the cloud editor")
    return latest


def resolve(target: str) -> mc.MulticamSession:
    """A local session id, or the cloud edit id a local session was sent as."""
    if re.fullmatch(r"[a-f0-9]{6,32}", target or ""):
        return mc.MulticamSession.load(target)
    for item in mc.list_sessions():
        session = mc.MulticamSession.load(item["session_id"])
        if (session.cloud or {}).get("id") == target:
            return session
    raise MulticamCloudError(
        f"No multicam edit on this computer was sent to the cloud as {target}. "
        "Pull it where it was prepared, with the camera files")


def pull(session: mc.MulticamSession) -> mc.MulticamSession:
    """Applies the cloud editor's cut, removals, look and names to this session, ready to render."""
    edit_id = (session.cloud or {}).get("id")
    if not edit_id:
        raise MulticamCloudError("this edit was never sent to podcli cloud. Send it with --cloud first")
    edit = _request("GET", f"/v1/multicam/{edit_id}/edit")
    state = edit.get("state") or {}

    names = {p.get("id"): p.get("name") for p in state.get("people", [])}
    if any(names.get(p.id) and names[p.id] != p.name for p in session.people):
        session = mc.update_mapping(session, {"people": [
            {"id": p.id, "name": names.get(p.id) or p.name, "role": p.role} for p in session.people
        ]})
    if edit.get("look"):
        session = mc.update_mapping(session, {"look": edit["look"]})
    cuts = edit.get("cuts") or state.get("cuts")
    if cuts:
        session = mc.set_cuts(session, cuts)
    if isinstance(edit.get("removals"), list):
        session = mc.set_removals(session, [{"start": r["start"], "end": r["end"]} for r in edit["removals"]])
    return session
