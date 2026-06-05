"""Attribution (clip ↔ uploaded video) and metric sync.

podcli renders but never publishes, so performance is reconstructed after the
fact: match each rendered clip to its uploaded video, then pull metrics onto
the clip. Matching is proposed, never silent — a wrong link poisons the signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from services.clips_history import load_clips_history, update_clip
from . import client


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_links(limit: int = 50) -> list[dict[str, Any]]:
    """Best upload match per unlinked clip: duration proximity + title + recency."""
    uploads = client.list_uploads(limit=limit)
    proposals = []
    for clip in load_clips_history():
        if clip.get("youtube_video_id"):
            continue
        best, best_score = None, 0.0
        for up in uploads:
            dur_gap = abs((up.get("duration") or 0) - (clip.get("duration") or 0))
            dur_score = max(0.0, 1.0 - dur_gap / 5.0)  # within ~5s
            score = 0.6 * _ratio(clip.get("title", ""), up["title"]) + 0.4 * dur_score
            if score > best_score:
                best, best_score = up, score
        if best and best_score >= 0.4:
            proposals.append({
                "clip_id": clip["id"], "clip_title": clip.get("title"),
                "video_id": best["video_id"], "video_title": best["title"],
                "score": round(best_score, 2),
            })
    return proposals


def set_link(clip_id: str, video_id: str) -> bool:
    return update_clip(clip_id, youtube_video_id=video_id) is not None


def sync_metrics() -> int:
    """Pull live metrics onto every linked clip. Returns the number updated."""
    count = 0
    for clip in load_clips_history():
        vid = clip.get("youtube_video_id")
        if not vid:
            continue
        metrics = client.fetch_metrics(vid)
        metrics["fetched_at"] = _now()
        if update_clip(clip["id"], metrics=metrics):
            count += 1
    from . import learnings
    learnings.write_learnings()
    return count


def sync_from_csv(path: str, threshold: float = 0.6) -> dict[str, Any]:
    """Match a YouTube Studio CSV to clips by title and write metrics. No auth."""
    rows = client.parse_analytics_csv(path)
    matched, unmatched = 0, []
    for clip in load_clips_history():
        best, best_score = None, 0.0
        for row in rows:
            r = _ratio(clip.get("title", ""), row["title"])
            if r > best_score:
                best, best_score = row, r
        if best and best_score >= threshold:
            metrics = {k: best[k] for k in ("views", "retention", "ctr", "impressions") if k in best}
            metrics["fetched_at"] = _now()
            update_clip(clip["id"], metrics=metrics)
            matched += 1
        else:
            unmatched.append(clip.get("title"))
    from . import learnings
    learnings_path = learnings.write_learnings()
    return {"matched": matched, "unmatched": unmatched, "rows": len(rows), "learnings": learnings_path}
