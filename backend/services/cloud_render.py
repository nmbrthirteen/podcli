"""
Cutting an episode on podcli.com instead of on this machine.

The engine here and the engine the cloud worker runs are the same code, so this
module renders nothing. It uploads the recording, queues the render the studio
would have queued, follows it, and brings the finished clips back down to the
same output folder a local run would have written.

The chain is the one the web studio uses:

    POST /v1/jobs/sources            reserve a place and get somewhere to PUT to
    PUT  <signed url>                the bytes, straight to object storage
    POST /v1/jobs/sources/:id/complete
    POST /v1/jobs                    queue the render
    GET  /v1/jobs/:id                follow it
    GET  /v1/episodes                find what it made
"""

import os
import sys
import time
import urllib.error
import urllib.request

from services import podcli_cloud

ACCENT = "\033[38;2;212;135;74m"
GRAY = "\033[38;5;245m"
RESET = "\033[0m"

POLL_SECONDS = 3
# A queue can be long and an episode is an hour of video. This is a ceiling on
# waiting, not an expectation of it.
MAX_WAIT_SECONDS = 6 * 3600


class CloudRenderError(Exception):
    pass


def run(video_path: str, config: dict, output_dir: str, args) -> None:
    """Render one episode in the cloud and download the clips it produced."""
    if not podcli_cloud.signed_in():
        raise CloudRenderError(
            "cloud rendering needs a podcli Pro session — run `podcli login`")

    size = os.path.getsize(video_path)
    print(f"\n  podcli — rendering in the cloud")
    print(f"  Video:   {os.path.basename(video_path)} ({_megabytes(size)})")
    print(f"  Output:  {output_dir}/\n")

    source_id = _upload(video_path, size)
    job = podcli_cloud.request("POST", "/v1/jobs", {
        "sourceId": source_id,
        "kind": "render",
        "params": _params(config, args),
        **({"templateId": args.template_id} if getattr(args, "template_id", None) else {}),
    }, timeout=60)

    print(f"  Queued as {GRAY}{job['id']}{RESET}")
    finished = _follow(job["id"])

    made = (finished.get("result") or {}).get("clips")
    print(f"\n  Rendered {made if made is not None else 'the'} "
          f"clip{'' if made == 1 else 's'} in the cloud.")
    downloaded = _download_clips(source_id, output_dir)
    if downloaded:
        print(f"  Saved {downloaded} file{'' if downloaded == 1 else 's'} to {output_dir}/")
    else:
        print(f"  {GRAY}Nothing to download yet — see them at podcli.com/app{RESET}")


def _params(config: dict, args) -> dict:
    """
    The look, in the shape the worker takes it.

    Only what the cloud understands is sent. Everything else in a local config,
    such as a logo file on this disk, means nothing to a machine that cannot
    see this disk, and the workspace's own template covers it there.
    """
    params = {
        "topN": config.get("top_clips"),
        "captionStyle": config.get("caption_style"),
        "crop": config.get("crop_strategy"),
        "format": config.get("format"),
        "quality": config.get("quality"),
        "profile": config.get("profile"),
        "thumbnails": config.get("generate_thumbnails"),
    }
    if config.get("fast_mode"):
        params["fast"] = True
    return {key: value for key, value in params.items() if value is not None}


def _upload(video_path: str, size: int) -> str:
    opened = podcli_cloud.request("POST", "/v1/jobs/sources", {
        "filename": os.path.basename(video_path),
        "sizeBytes": size,
    }, timeout=60)

    multipart = opened.get("multipart")
    if not multipart:
        with open(video_path, "rb") as fh:
            _put(opened["uploadUrl"], fh.read(), size, 1, 1)
        parts = None
    else:
        parts = _upload_parts(video_path, size, multipart)

    podcli_cloud.request(
        "POST", f"/v1/jobs/sources/{opened['sourceId']}/complete",
        {"parts": parts} if parts else {}, timeout=120)
    print(f"\r  Uploaded {_megabytes(size)}{' ' * 24}")
    return opened["sourceId"]


def _upload_parts(video_path: str, size: int, multipart: dict) -> list:
    part_size, urls = multipart["partSize"], multipart["urls"]
    parts = []
    with open(video_path, "rb") as fh:
        for index, url in enumerate(urls, start=1):
            chunk = fh.read(part_size)
            if not chunk:
                break
            # S3 identifies a finished part by the ETag it answers the PUT with,
            # so a part uploaded without reading that header cannot be joined.
            etag = _put(url, chunk, size, index, len(urls))
            if not etag:
                raise CloudRenderError(
                    f"storage did not return an ETag for part {index}, so the "
                    "upload cannot be completed")
            parts.append({"partNumber": index, "etag": etag})
    return parts


def _put(url: str, body: bytes, total: int, index: int, count: int) -> str:
    done = min(total, index * len(body)) if count > 1 else total
    print(f"\r  Uploading {_megabytes(done)} of {_megabytes(total)}"
          f"{f' (part {index}/{count})' if count > 1 else ''}…",
          end="", flush=True)

    req = urllib.request.Request(url, data=body, method="PUT",
                                 headers={"content-type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as response:
            return (response.headers.get("ETag") or "").strip('"')
    except urllib.error.HTTPError as exc:
        raise CloudRenderError(f"upload failed: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise CloudRenderError(f"upload failed: {exc.reason}") from None


def _follow(job_id: str) -> dict:
    """Print what the worker is doing until it stops doing it."""
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            job = podcli_cloud.request("GET", f"/v1/jobs/{job_id}", timeout=30)
        except podcli_cloud.CloudError as exc:
            # A render that takes an hour will outlive the odd bad minute. Only
            # an answer that will not change is worth giving up on.
            if exc.retryable or not exc.status:
                continue
            raise CloudRenderError(str(exc)) from None

        status = job.get("status")
        if status in ("done", "failed", "canceled"):
            print("\r" + " " * len(last) + "\r", end="")
            if status != "done":
                raise CloudRenderError(job.get("error") or f"the render was {status}")
            return job

        line = (f"  {ACCENT}{job.get('progress', 0)}%{RESET} "
                f"{job.get('stage') or status}"
                f"{' — ' + job['detail'] if job.get('detail') else ''}")
        print("\r" + " " * len(last) + "\r" + line, end="", flush=True)
        last = line

    raise CloudRenderError("gave up waiting; the render is still running at podcli.com/app")


def _download_clips(source_id: str, output_dir: str) -> int:
    """
    Bring down what the render made.

    Found through the episode rather than through the job: the job's result
    counts the clips, and the episode is what they hang off.
    """
    episodes = podcli_cloud.request("GET", "/v1/episodes", timeout=60).get("episodes", [])
    episode = next((e for e in episodes if e.get("source_id") == source_id), None)
    if not episode:
        return 0

    detail = podcli_cloud.request("GET", f"/v1/episodes/{episode['id']}", timeout=60)
    saved = 0
    for clip in detail.get("clips", []):
        if not clip.get("video"):
            continue
        name = _filename(clip, saved + 1)
        try:
            _download(clip["video"], os.path.join(output_dir, name))
        except CloudRenderError as exc:
            print(f"  {GRAY}{name}: {exc}{RESET}")
            continue
        saved += 1
    return saved


def _filename(clip: dict, position: int) -> str:
    title = "".join(
        char if char.isalnum() or char in " -_" else "_"
        for char in (clip.get("title") or "clip")
    ).strip().replace(" ", "_")[:60]
    return f"{position:02d}_{title or 'clip'}.mp4"


def _download(url: str, path: str) -> None:
    print(f"\r  Downloading {os.path.basename(path)}…{' ' * 12}", end="", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=900) as response, \
                open(path, "wb") as fh:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
    except urllib.error.HTTPError as exc:
        raise CloudRenderError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise CloudRenderError(str(exc.reason)) from None
    finally:
        print("\r" + " " * 60 + "\r", end="")


def _megabytes(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    return f"{size / (1 << 20):.0f} MB"


def main(video_path: str, config: dict, output_dir: str, args) -> None:
    """Entry point for the CLI, which reports failures rather than raising."""
    try:
        run(video_path, config, output_dir, args)
    except CloudRenderError as exc:
        print(f"\n  Cloud render failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except podcli_cloud.CloudError as exc:
        print(f"\n  Cloud render failed: {exc}", file=sys.stderr)
        sys.exit(1)
