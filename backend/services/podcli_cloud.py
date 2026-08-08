"""Client for podcli Pro's hosted API.

This module is the whole of Pro that lives in the open source app: where the
token is kept, how it is sent, and what shape the request takes. There is no
secret here and nothing to crack — the server decides entitlement, so a patched
client gets a UI that says Pro and an HTTP 401.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from config.paths import paths

DEFAULT_API_URL = "https://api.podcli.com"
AUTH_FILENAME = "auth.json"


def api_url() -> str:
    return (os.environ.get("PODCLI_API_URL") or DEFAULT_API_URL).rstrip("/")


def _auth_path() -> str:
    return os.path.join(paths["home"], AUTH_FILENAME)


def read_token() -> Optional[str]:
    """The session token, from the environment or the file `podcli login` wrote."""
    env = (os.environ.get("PODCLI_TOKEN") or "").strip()
    if env:
        return env
    try:
        with open(_auth_path(), encoding="utf-8") as fh:
            token = (json.load(fh).get("token") or "").strip()
            return token or None
    except (OSError, ValueError):
        return None


def _auth_data() -> dict:
    try:
        with open(_auth_path(), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _write_auth(data: dict) -> None:
    os.makedirs(paths["home"], exist_ok=True)
    path = _auth_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_token(token: str, workspace_id: str = "") -> None:
    _write_auth({"token": token, "workspace_id": workspace_id})


PAID_PLANS = ("pro", "team", "studio", "agency")
PLAN_TTL_SECONDS = 6 * 3600


def remember_plan(plan: str) -> None:
    """Cache what the server last said this workspace is entitled to."""
    data = _auth_data()
    if not data.get("token"):
        return
    data["plan"] = (plan or "").strip().lower()
    data["plan_checked_at"] = time.time()
    _write_auth(data)


def entitled() -> bool:
    """
    False only when the workspace is known to have no subscription.

    Unknown and stale both mean "try": a subscription bought a minute ago has to
    work without signing out first, and the server is the only real authority.
    """
    data = _auth_data()
    plan = (data.get("plan") or "").strip().lower()
    if not plan:
        return True
    if time.time() - float(data.get("plan_checked_at") or 0) > PLAN_TTL_SECONDS:
        return True
    return plan in PAID_PLANS


def clear_token() -> None:
    try:
        os.unlink(_auth_path())
    except OSError:
        pass


def signed_in() -> bool:
    return read_token() is not None


class CloudError(Exception):
    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def request(method: str, path: str, body: Optional[dict] = None,
            timeout: int = 300) -> Any:
    token = read_token()
    if not token:
        raise CloudError("not signed in — run `podcli login`", status=401)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{api_url()}{path}",
        data=data,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            **({"content-type": "application/json"} if data else {}),
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail, retryable = _describe(exc)
        raise CloudError(detail, status=exc.code, retryable=retryable) from None
    except urllib.error.URLError as exc:
        raise CloudError(f"could not reach {api_url()}: {exc.reason}",
                         retryable=True) from None


def _describe(exc: urllib.error.HTTPError) -> tuple[str, bool]:
    """Turn an HTTP failure into something a user can act on."""
    payload: dict = {}
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        pass
    detail = payload.get("error")
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("message", item)) for item in detail)

    if exc.code == 401:
        return ("podcli Pro session expired — run `podcli login` again", False)
    if exc.code == 402:
        return ("this workspace has no active podcli Pro subscription", False)
    if exc.code == 403:
        return (detail or "your role does not allow this", False)
    if exc.code == 429:
        used, cap = payload.get("used"), payload.get("cap")
        if used is not None and cap is not None:
            return (f"monthly limit reached ({used}/{cap} episodes)", False)
        return (detail or "rate limited, try again shortly", True)
    if exc.code >= 500 or exc.code == 503:
        return (detail or "podcli Pro is temporarily unavailable", True)
    return (detail or f"podcli Pro returned HTTP {exc.code}", False)


def generate(purpose: str, instruction: str, *, system: Optional[str] = None,
             cached_context: Optional[str] = None,
             episode_source_hash: Optional[str] = None,
             max_tokens: int = 16000, timeout: int = 300) -> dict:
    body: dict[str, Any] = {
        "purpose": purpose,
        "instruction": instruction,
        "maxTokens": max_tokens,
    }
    if system:
        body["system"] = system
    if cached_context:
        body["cachedContext"] = cached_context
    if episode_source_hash:
        body["episodeSourceHash"] = episode_source_hash
    return request("POST", "/v1/ai/generate", body, timeout=timeout)


def source_hash(video_path: str) -> Optional[str]:
    """Identify an episode across machines.

    Must stay byte-identical to the TypeScript implementation in
    src/services/podcli-cloud.ts — the two clients hash the same file and the
    server dedupes episodes on the result, so any divergence silently splits one
    episode into two. First 8 MB only: distinctive enough, and digesting a 2 GB
    master on every render is not.
    """
    import hashlib

    digest = hashlib.sha256()
    remaining = 8 * 1024 * 1024
    try:
        with open(video_path, "rb") as fh:
            while remaining > 0:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return None
    return digest.hexdigest()[:32]


def register_clip(clip: dict) -> Optional[dict]:
    return request("POST", "/v1/clips", clip, timeout=60)


def backfill_clips(limit: int = 200) -> tuple[int, int]:
    """Push locally-recorded clips that never reached the workspace.

    Runs at sign-in so a new subscriber's back catalogue is behind the
    performance model from their first session, rather than the model starting
    empty and staying useless for months.
    """
    from services.clips_history import load_clips_history, update_clip

    synced = failed = 0
    for entry in load_clips_history():
        if synced + failed >= limit:
            break
        if entry.get("cloud_id"):
            continue
        source = entry.get("source_video")
        if not source or not os.path.exists(source):
            continue

        digest = source_hash(source)
        if not digest:
            failed += 1
            continue

        try:
            result = register_clip({
                "sourceHash": digest,
                "episodeTitle": os.path.basename(source),
                "title": entry.get("title"),
                "startSecond": entry.get("start_second"),
                "endSecond": entry.get("end_second"),
                "durationSec": entry.get("duration"),
                "contentType": entry.get("content_type"),
                "captionStyle": entry.get("caption_style"),
                "aspectRatio": entry.get("format"),
                "transcriptSlice": entry.get("transcript_slice"),
            })
        except CloudError:
            failed += 1
            continue

        if result and result.get("id"):
            update_clip(entry["id"], cloud_id=result["id"], cloud_synced=True)
            synced += 1
        else:
            failed += 1

    return synced, failed


def prompt_block() -> str:
    """What this workspace has learned, phrased for the selection prompt.

    Rendered server-side rather than assembled here, so improving how a
    workspace's history is presented to the model is a deploy rather than
    something that waits for every user to upgrade their CLI.

    Short timeout and silent on failure: better clips are the point, but not at
    the cost of blocking a suggestion run behind a slow network.
    """
    if not signed_in():
        return ""
    try:
        payload = request("GET", "/v1/insights/prompt-block", timeout=10)
    except CloudError:
        return ""
    return (payload or {}).get("block") or ""


def list_workspaces() -> list[dict]:
    return (request("GET", "/v1/workspaces", timeout=30) or {}).get("workspaces", [])


def create_workspace(name: str) -> dict:
    payload = request("POST", "/v1/workspaces", {"name": name}, timeout=30)
    write_token(payload["token"], payload["id"])
    return payload


def switch_workspace(workspace_id: str) -> dict:
    """Switching means a new session, not a mutable field on the old one.

    Tenancy is decided once, at authentication, from the session's workspace —
    so a token can never be pointed at a workspace it was not issued for.
    """
    payload = request("POST", f"/v1/workspaces/{workspace_id}/session", {}, timeout=30)
    write_token(payload["token"], payload["workspaceId"])
    return payload


def me() -> dict:
    return request("GET", "/v1/auth/me", timeout=30)


def login(email: str, password: str) -> dict:
    """Exchange credentials for a session token. Does not require an existing one."""
    data = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url()}/v1/auth/login", data=data, method="POST",
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail, _ = _describe(exc)
        raise CloudError(detail, status=exc.code) from None
    except urllib.error.URLError as exc:
        raise CloudError(f"could not reach {api_url()}: {exc.reason}") from None

    write_token(payload["token"], payload.get("workspaceId", ""))
    return payload
