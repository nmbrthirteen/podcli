"""YouTube Data + Analytics client and CSV importer.

Two ways to get per-video performance into podcli:
  1. OAuth (bring-your-own Google Cloud creds) — full, live metrics.
  2. CSV export from YouTube Studio — no auth, works offline.

The google-api libraries are imported lazily so the rest of podcli runs
without them; OAuth paths raise a clear, actionable error when they're absent.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any, Optional

from config.paths import paths

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
_TOKEN_PATH = os.path.join(os.path.dirname(paths["integrations"]), "youtube-token.json")


def _yt_config() -> dict[str, Any]:
    """The 'youtube' block of integrations.json (client_id/client_secret/...)."""
    try:
        with open(paths["integrations"]) as f:
            return (json.load(f).get("youtube") or {})
    except Exception:
        return {}


def is_authorized() -> bool:
    return os.path.exists(_TOKEN_PATH)


def _require_libs():
    try:
        from googleapiclient.discovery import build  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google.auth.transport.requests import Request  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "YouTube API libraries are not installed. Run:\n"
            "  pip install google-api-python-client google-auth-oauthlib\n"
            "Or import a YouTube Studio analytics CSV instead (no auth needed)."
        ) from e


def _credentials():
    """Load cached creds, refreshing or running the loopback flow as needed."""
    _require_libs()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(_TOKEN_PATH, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        cfg = _yt_config()
        client_id, client_secret = cfg.get("client_id"), cfg.get("client_secret")
        if not (client_id and client_secret):
            raise RuntimeError(
                "Missing YouTube OAuth client. Add client_id + client_secret to the "
                "'youtube' block of .podcli/integrations.json (from a Google Cloud "
                "OAuth desktop client), then run: podcli youtube auth"
            )
        flow = InstalledAppFlow.from_client_config(
            {"installed": {
                "client_id": client_id, "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }},
            SCOPES,
        )
        creds = flow.run_local_server(port=0)
    # 0o600: the file holds a long-lived refresh token — keep it owner-only.
    fd = os.open(_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    return creds


def authorize() -> bool:
    """Run the loopback OAuth flow and cache the token. Returns True on success."""
    _credentials()
    return True


def _iso8601_to_seconds(d: str) -> float:
    """PT#M#S → seconds."""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0.0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def list_uploads(limit: int = 50) -> list[dict[str, Any]]:
    """Recent uploads on the authorized channel: id, title, published_at, duration."""
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", credentials=_credentials())
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=min(limit, 50)).execute()
    ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
    if not ids:
        return []
    vids = yt.videos().list(part="snippet,contentDetails", id=",".join(ids)).execute()
    out = []
    for v in vids.get("items", []):
        out.append({
            "video_id": v["id"],
            "title": v["snippet"]["title"],
            "published_at": v["snippet"]["publishedAt"],
            "duration": _iso8601_to_seconds(v["contentDetails"]["duration"]),
            "thumbnail": (v["snippet"].get("thumbnails", {}).get("high") or {}).get("url"),
        })
    return out


def fetch_metrics(video_id: str) -> dict[str, Any]:
    """Lifetime metrics for one video via the Analytics API + Data API stats."""
    from googleapiclient.discovery import build
    creds = _credentials()
    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    res = analytics.reports().query(
        ids="channel==MINE", startDate="2005-01-01", endDate="2099-01-01",
        metrics="views,averageViewPercentage,estimatedMinutesWatched", filters=f"video=={video_id}",
    ).execute()
    rows = res.get("rows") or [[0, 0, 0]]
    views, retention, _watch = rows[0]
    metrics = {"views": int(views), "retention": round(float(retention), 1)}
    try:
        imp = analytics.reports().query(
            ids="channel==MINE", startDate="2005-01-01", endDate="2099-01-01",
            metrics="impressions,impressionsClickThroughRate", filters=f"video=={video_id}",
        ).execute()
        irows = imp.get("rows") or [[0, 0]]
        metrics["impressions"] = int(irows[0][0])
        metrics["ctr"] = round(float(irows[0][1]), 1)
    except Exception:
        pass
    return metrics


# ── CSV import (no auth) ──────────────────────────────────────────────

_CSV_ALIASES = {
    "title": ["video title", "title", "content"],
    "views": ["views"],
    "retention": ["average percentage viewed (%)", "average percentage viewed", "average view percentage"],
    "ctr": ["impressions click-through rate (%)", "impressions click-through rate"],
    "impressions": ["impressions"],
}


def parse_analytics_csv(path: str) -> list[dict[str, Any]]:
    """Parse a YouTube Studio analytics CSV into rows of {title, views, retention, ctr, impressions}."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {h.lower().strip(): h for h in (reader.fieldnames or [])}

        def col(field: str) -> Optional[str]:
            for alias in _CSV_ALIASES[field]:
                if alias in headers:
                    return headers[alias]
            return None

        title_c = col("title")
        if not title_c:
            raise ValueError("CSV has no recognizable video-title column")
        out = []
        for row in reader:
            title = (row.get(title_c) or "").strip()
            if not title or title.lower() in ("total", "totals"):
                continue
            rec: dict[str, Any] = {"title": title}
            for field in ("views", "retention", "ctr", "impressions"):
                c = col(field)
                if c and row.get(c) not in (None, ""):
                    try:
                        rec[field] = float(str(row[c]).replace(",", "").replace("%", ""))
                    except ValueError:
                        pass
            out.append(rec)
        return out
