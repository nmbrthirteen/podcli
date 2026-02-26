"""
Clip generator — orchestrates the full pipeline for creating a short-form clip.

Pipeline: cut segment → crop to 9:16 → render captions → burn captions
          (+ optional gradient overlay + logo) → normalize audio
"""

import os
import sys
import tempfile
import shutil
from typing import Optional, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.caption_renderer import render_captions
from services.video_processor import (
    cut_segment,
    crop_to_vertical,
    burn_captions,
    normalize_audio,
)
from config.caption_styles import get_style


def generate_clip(
    video_path: str,
    start_second: float,
    end_second: float,
    caption_style: str = "hormozi",
    crop_strategy: str = "center",
    transcript_words: list[dict] = None,
    title: str = "clip",
    output_dir: Optional[str] = None,
    logo_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Generate a complete short-form video clip.

    Args:
        video_path: Path to source podcast video
        start_second: Clip start time
        end_second: Clip end time
        caption_style: "hormozi", "karaoke", "subtle", or "branded"
        crop_strategy: "center" or "face"
        transcript_words: Word-level timestamps from transcription
        title: Clip title (used in filename)
        output_dir: Where to save the final clip (defaults to temp)
        logo_path: Path to logo image (PNG). Used with "branded" style.
        progress_callback: Optional (percent, message) callback

    Returns:
        {
            "output_path": str,
            "duration": float,
            "file_size_mb": float,
            "title": str,
        }
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if end_second <= start_second:
        raise ValueError("end_second must be greater than start_second")

    duration = end_second - start_second
    if duration > 180:
        raise ValueError(f"Clip too long ({duration:.0f}s). Max 180 seconds for shorts.")

    # Load style config for branded-specific settings
    style_config = get_style(caption_style)

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="podcast_clip_")

    try:
        # Step 1: Cut the segment from the source video
        if progress_callback:
            progress_callback(10, "Cutting video segment...")

        segment_path = os.path.join(work_dir, "segment.mp4")
        cut_segment(video_path, segment_path, start_second, end_second)

        # Step 2: Crop to vertical 9:16
        if progress_callback:
            progress_callback(30, "Cropping to 9:16 vertical...")

        cropped_path = os.path.join(work_dir, "cropped.mp4")
        crop_to_vertical(segment_path, cropped_path, strategy=crop_strategy)

        # Step 3: Generate captions + burn (with optional gradient & logo)
        if transcript_words:
            if progress_callback:
                progress_callback(50, f"Rendering {caption_style} captions...")

            # Filter words that fall within our clip's time range
            clip_words = [
                w for w in transcript_words
                if w["start"] >= start_second and w["end"] <= end_second
            ]

            if clip_words:
                # Generate ASS subtitle file
                ass_path = os.path.join(work_dir, "captions.ass")
                render_captions(
                    words=clip_words,
                    caption_style=caption_style,
                    output_path=ass_path,
                    time_offset=start_second,
                )

                # Burn captions into video
                if progress_callback:
                    progress_callback(65, "Burning captions into video...")

                captioned_path = os.path.join(work_dir, "captioned.mp4")

                # Branded style: add gradient overlay + logo
                use_gradient = style_config.get("gradient_overlay", False)
                gradient_opacity = style_config.get("gradient_opacity", 0.6)
                use_logo = style_config.get("logo_support", False) and logo_path

                burn_captions(
                    input_path=cropped_path,
                    ass_path=ass_path,
                    output_path=captioned_path,
                    gradient_overlay=use_gradient,
                    gradient_opacity=gradient_opacity,
                    logo_path=logo_path if use_logo else None,
                    logo_height=style_config.get("logo_height", 80),
                    logo_margin_x=style_config.get("logo_margin_x", 30),
                    logo_margin_y=style_config.get("logo_margin_y", 40),
                )
            else:
                captioned_path = cropped_path
        else:
            captioned_path = cropped_path

        # Step 4: Normalize audio
        if progress_callback:
            progress_callback(80, "Normalizing audio to -14 LUFS...")

        normalized_path = os.path.join(work_dir, "normalized.mp4")
        normalize_audio(captioned_path, normalized_path)

        # Step 5: Move to output
        if progress_callback:
            progress_callback(95, "Finalizing output...")

        # Clean filename
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title)
        safe_title = safe_title.strip().replace(" ", "_")[:50]
        output_filename = f"{safe_title}_short.mp4"

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            final_path = os.path.join(output_dir, output_filename)
        else:
            final_path = os.path.join(work_dir, output_filename)

        shutil.copy2(normalized_path, final_path)

        # Get file size
        file_size = os.path.getsize(final_path)
        file_size_mb = round(file_size / (1024 * 1024), 2)

        if progress_callback:
            progress_callback(100, "Clip complete!")

        return {
            "output_path": final_path,
            "duration": round(duration, 2),
            "file_size_mb": file_size_mb,
            "title": title,
        }

    finally:
        # Clean up temp files (but not if output is in work_dir)
        if output_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
