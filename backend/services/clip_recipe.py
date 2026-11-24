"""Everything needed to render one clip again, saved next to the clip.

`podcli process` writes `<clip>.recipe.json` beside every clip it renders, plus
the words and face map it used under `<output>/.podcli/`, named by content so
a later run with a different transcript never changes what an earlier recipe
reads. `podcli render` reads a
recipe, applies changes, and renders a new version without touching the old
file, so an app or a person can iterate on a clip without rerunning the whole
episode.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any, Callable, Optional

RECIPE_VERSION = 1

SETTING_KEYS = (
    "caption_style",
    "caption_position",
    "caption_font_scale",
    "captions",
    "logo_path",
    "logo_position",
    "logo_scale",
    "intro_path",
    "outro_path",
    "bookend_fade",
    "name_card",
    "motion",
    "topic",
    "progress",
    "brand",
    "theme",
    "font_family",
    "crop_strategy",
    "format",
    "allow_ass_fallback",
    "use_ass_captions",
)

CLIP_KEYS = ("start_second", "end_second", "title", "keep_segments", "hook", "quality", "trim_opening")


def snapshot_words(output_dir: str, words: list, face_map: Optional[dict]) -> str:
    """Save the words and face map once per distinct content and return the path."""
    payload = json.dumps({"words": words or [], "face_map": face_map}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    folder = os.path.join(output_dir, ".podcli")
    os.makedirs(folder, exist_ok=True)
    n = 1
    while True:
        path = os.path.join(folder, f"{digest}.words.json" if n == 1 else f"{digest}-{n}.words.json")
        if not os.path.exists(path):
            staged = f"{path}.{os.getpid()}.tmp"
            with open(staged, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(staged, path)
            return path
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.read() == payload:
                return path
        n += 1


def recipe_path_for(output_path: str) -> str:
    return os.path.splitext(output_path)[0] + ".recipe.json"


def build(
    *,
    video_path: str,
    clip: dict,
    settings: dict,
    result: dict,
    words_path: Optional[str],
    quality: Optional[str],
    previous: Optional[str] = None,
) -> dict:
    return {
        "version": RECIPE_VERSION,
        "video": os.path.abspath(video_path),
        "words": os.path.abspath(words_path) if words_path else None,
        "output": os.path.abspath(result["output_path"]),
        "previous": previous,
        "start_second": clip["start_second"],
        "end_second": clip["end_second"],
        "title": clip.get("title") or "clip",
        "keep_segments": clip.get("segments") or clip.get("keep_segments"),
        "hook": clip.get("hook"),
        "trim_opening": clip.get("trim_opening"),
        "quality": quality,
        "settings": {key: settings.get(key) for key in SETTING_KEYS if settings.get(key) is not None},
    }


def save(recipe: dict) -> str:
    path = recipe_path_for(recipe["output"])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(recipe, fh, ensure_ascii=False, indent=2)
    return path


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        recipe = json.load(fh)
    if not isinstance(recipe, dict):
        raise ValueError(f"{os.path.basename(path)} is not a clip recipe")
    if recipe.get("version") != RECIPE_VERSION:
        raise ValueError(f"{os.path.basename(path)} is recipe version {recipe.get('version')}, this podcli reads {RECIPE_VERSION}")
    return recipe


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def apply_changes(recipe: dict, changes: dict) -> dict:
    """A copy of the recipe with `changes` applied. Unknown keys are refused so a
    typo never renders a clip that silently ignored the request."""
    updated = json.loads(json.dumps(recipe))
    for key, value in changes.items():
        if key in CLIP_KEYS:
            updated[key] = value
        elif key in SETTING_KEYS:
            if value is None:
                updated["settings"].pop(key, None)
            else:
                updated["settings"][key] = value
        else:
            raise ValueError(f"unknown recipe field: {key}")
    if "start_second" in changes and "trim_opening" not in changes:
        # A start someone picked by hand is the start they want, so the sentence
        # snap and weak-opening trim stay off for this version.
        updated["trim_opening"] = False
    for edge in ("start_second", "end_second"):
        value = updated.get(edge)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{edge} must be a number of seconds")
    if updated["start_second"] < 0:
        raise ValueError("start_second can't be before the episode starts")
    if float(updated["end_second"]) <= float(updated["start_second"]):
        raise ValueError("end_second must be after start_second")
    updated["keep_segments"] = _fit_segments(updated.get("keep_segments"), updated["start_second"], updated["end_second"])
    return updated


def _fit_segments(segments: Optional[list], start: float, end: float) -> Optional[list]:
    """Trim kept pieces to the clip's new edges. A trim can cut a multi-piece
    clip down to nothing kept, which renders the plain range instead."""
    if not segments:
        return None
    fitted = []
    for piece in segments:
        a = max(float(piece["start"]), float(start))
        b = min(float(piece["end"]), float(end))
        if b - a > 0.05:
            fitted.append({**piece, "start": a, "end": b})
    return fitted or None


def _load_words(recipe: dict) -> tuple[list, Optional[dict]]:
    words_file = recipe.get("words")
    if words_file and os.path.exists(words_file):
        with open(words_file, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("words") or [], data.get("face_map")
    from services.transcript_packer import load_cached_transcript_for_video

    cached = load_cached_transcript_for_video(recipe["video"]) or {}
    return cached.get("words") or [], cached.get("face_map")


def _valid_hook(recipe: dict) -> Optional[dict]:
    if not recipe.get("hook"):
        return None
    from services.opening_hook import validate_hook

    try:
        return validate_hook(recipe["hook"], recipe["start_second"], recipe["end_second"], recipe.get("keep_segments"))
    except ValueError:
        return None


def render(
    recipe: dict,
    *,
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> tuple[dict, str]:
    """Render a new version of the clip. Returns the render result and the path
    of the new version's recipe. The previous file is left in place."""
    if not os.path.exists(recipe["video"]):
        raise FileNotFoundError(f"source video not found: {recipe['video']}")
    if recipe.get("quality"):
        os.environ["PODCLI_QUALITY"] = recipe["quality"]
    from services.clip_generator import generate_clip, keep_existing_outputs

    words, face_map = _load_words(recipe)
    hook = _valid_hook(recipe)
    settings = dict(recipe.get("settings") or {})
    theme = settings.pop("theme", None)
    target_dir = output_dir or os.path.dirname(recipe["output"])
    os.makedirs(target_dir, exist_ok=True)
    keep_existing_outputs(target_dir, recipe.get("title") or "clip")
    result = generate_clip(
        video_path=recipe["video"],
        start_second=float(recipe["start_second"]),
        end_second=float(recipe["end_second"]),
        transcript_words=words,
        title=recipe.get("title") or "clip",
        output_dir=target_dir,
        face_map=face_map,
        keep_segments=recipe.get("keep_segments"),
        hook=hook,
        trim_opening=recipe.get("trim_opening"),
        theme=theme,
        progress_callback=progress_callback,
        **settings,
    )
    clip = {
        "start_second": recipe["start_second"],
        "end_second": recipe["end_second"],
        "title": recipe.get("title"),
        "keep_segments": recipe.get("keep_segments"),
        "hook": hook,
        "trim_opening": recipe.get("trim_opening"),
    }
    next_recipe = build(
        video_path=recipe["video"],
        clip=clip,
        settings={**settings, "theme": theme},
        result=result,
        words_path=recipe.get("words"),
        quality=recipe.get("quality"),
        previous=recipe.get("output"),
    )
    return result, save(next_recipe)
