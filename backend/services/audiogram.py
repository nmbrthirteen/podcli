"""Episodes that were never filmed.

A podcast recorded as audio has no frame to crop, no face to follow and nothing
to burn captions onto. Everything downstream of the transcript assumes
otherwise: `get_dimensions` raises "No video stream found". It raises it after
transcription, so an hour of audio pays for its full Whisper run to be told that
it was never going to produce a clip, and the message it gets reads like a bug
in the tool rather than a fact about the file.

The header knew all along. This is the part that reads it.
"""

import os

from services.media_probe import get_video_info


def _moving_picture(streams: list) -> bool:
    """Whether any stream is something you could actually crop."""
    for stream in streams or []:
        if stream.get("codec_type") != "video":
            continue
        # Cover art in an mp3 is a video stream by ffprobe's reckoning, and a
        # single still is not something to crop or follow a face around.
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        if str(stream.get("avg_frame_rate") or "0/0").split("/")[0] in ("0", ""):
            continue
        return True
    return False


def is_audio_only(path: str) -> bool:
    """Positively sound, positively no picture.

    Deliberately stricter than "no video found", because this one is allowed to
    stop a run. A file ffprobe cannot read at all is not evidence of anything:
    it might be a stub, a truncated download, or a container this build has no
    demuxer for. Refusing those with a message about audio would swap one
    unhelpful failure for a differently unhelpful one, so anything short of "I
    read this, it has sound, and it has no moving picture" is left to the path
    it has always taken.
    """
    try:
        info = get_video_info(path)
    except Exception:
        return False
    streams = info.get("streams") or []
    if not streams:
        return False
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        return False
    return not _moving_picture(streams)


def audio_only_message(path: str) -> str:
    """What to tell somebody whose file has no picture in it."""
    return (
        f"{os.path.basename(path)} has no video track, so there is nothing to crop "
        "or burn captions onto. Clips are made from video; pass the recording that "
        "has the picture in it."
    )
