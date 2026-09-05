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
import urllib.parse
import urllib.request
from typing import Any, Optional

from config.paths import paths

DEFAULT_API_URL = "https://api.podcli.com"
AUTH_FILENAME = "auth.json"


def api_url() -> str:
    """
    The API base, restricted to http and https.

    urlopen honours whatever scheme it is given, so an unchecked value here
    lets `file:` turn a local path into what the code treats as an API
    response.
    """
    raw = (os.environ.get("PODCLI_API_URL") or DEFAULT_API_URL).rstrip("/")
    if urllib.parse.urlparse(raw).scheme not in ("http", "https"):
        return DEFAULT_API_URL
    return raw


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
    # Opened with the mode already set rather than chmod'd afterwards: the
    # session token would otherwise be world-readable for the width of the
    # write, and a file that already existed would keep its old mode until the
    # chmod landed.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
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


def revoke_session() -> None:
    """
    End this machine's session at the server, not just on disk.

    Deleting the file alone leaves the token live for its full month, so a
    laptop signed out because it was about to be lost stays signed in.
    """
    request("POST", "/v1/auth/logout", {}, timeout=15)


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
        parsed = json.loads(exc.read().decode("utf-8"))
        # A server can answer with a list or a bare string. Assuming an object
        # turns the error path itself into an AttributeError.
        if isinstance(parsed, dict):
            payload = parsed
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
        except CloudError as exc:
            failed += 1
            # An expired session or a workspace with no subscription answers the
            # same way for every remaining clip. Continuing would hash and
            # upload another few hundred megabytes to be refused each time.
            if exc.status in (401, 402, 403):
                break
            continue

        if result and result.get("id"):
            update_clip(entry["id"], cloud_id=result["id"], cloud_synced=True)
            synced += 1
        else:
            failed += 1

    return synced, failed


_PROMPT_BLOCK_CACHE: dict[str, str] = {}


def prompt_block() -> str:
    """What this workspace has learned, phrased for the selection prompt.

    Rendered server-side rather than assembled here, so improving how a
    workspace's history is presented to the model is a deploy rather than
    something that waits for every user to upgrade their CLI.

    Short timeout and silent on failure: better clips are the point, but not at
    the cost of blocking a suggestion run behind a slow network. Held for the
    life of the process, keyed on the token so a workspace switch cannot serve
    one workspace's learnings into another's prompt.
    """
    token = read_token()
    if not token:
        return ""
    if token in _PROMPT_BLOCK_CACHE:
        return _PROMPT_BLOCK_CACHE[token]
    try:
        payload = request("GET", "/v1/insights/prompt-block", timeout=10)
        block = (payload or {}).get("block") or ""
    except Exception:
        # Never raises: a stall gives TimeoutError and a non-JSON 200 gives
        # JSONDecodeError, neither of them a CloudError.
        block = ""
    _PROMPT_BLOCK_CACHE[token] = block
    return block


def templates() -> list[dict]:
    """The looks this account cuts in.

    Templates are a Pro feature and they belong to the account, not to a
    machine: signed in on a laptop, `--template "Bold cuts"` means the same
    thing it means in the studio. The look itself is still only the flags this
    CLI already takes, so nothing about a free, offline render changes.
    """
    payload = request("GET", "/v1/templates", timeout=30) or {}
    return payload.get("templates", [])


def find_template(name_or_id: str) -> Optional[dict]:
    """Match on id first, then on name, case-insensitively."""
    wanted = (name_or_id or "").strip()
    if not wanted:
        return None

    found = templates()
    for template in found:
        if template.get("id") == wanted:
            return template
    for template in found:
        if (template.get("name") or "").lower() == wanted.lower():
            return template
    return None


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


def start_cli_auth(label: str) -> dict:
    """
    Open a sign-in that the browser will approve.

    Returns the device code the CLI keeps to itself, the short code the person
    matches across the two screens, and the link to approve it at. No existing
    session is needed, and no credential is taken here.
    """
    return _unauthenticated("POST", "/v1/auth/cli", {"label": label})


def poll_cli_auth(device_code: str) -> Optional[dict]:
    """
    Ask whether the browser has approved yet.

    None means keep waiting. A dict is the session, and is written to disk
    before returning so a crash between here and the caller cannot lose the one
    token this device code will ever mint.
    """
    payload = _unauthenticated("POST", "/v1/auth/cli/poll", {"deviceCode": device_code})
    if payload.get("status") == "pending":
        return None
    write_token(payload["token"], payload.get("workspaceId", ""))
    return payload


def _unauthenticated(method: str, path: str, body: dict) -> dict:
    """Like `request`, for the endpoints that run before there is a session."""
    req = urllib.request.Request(
        f"{api_url()}{path}", data=json.dumps(body).encode("utf-8"), method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail, retryable = _describe(exc)
        raise CloudError(detail, status=exc.code, retryable=retryable) from None
    except urllib.error.URLError as exc:
        raise CloudError(f"could not reach {api_url()}: {exc.reason}") from None
