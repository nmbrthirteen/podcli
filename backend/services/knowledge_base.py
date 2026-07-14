"""Knowledge base helpers shared by the CLI and the AI prompt builders."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from config.paths import paths

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "knowledge"

# The studio writes README.md into the knowledge dir; it is not user content.
IGNORED_FILES = {"readme.md"}

_PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,120}\]")
_MD_LINK = re.compile(r"\[[^\]\n]+\]\([^)\n]*\)")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_TABLE_RULE = set("|-: ")

# Tuned against the shipped templates (line ratio 0.62-0.92, char ratio 0.34-0.73) and
# against filled files, which keep the odd legitimate bracket ("[guest]", "[00:00]").
MIN_PLACEHOLDERS = 3
PLACEHOLDER_LINE_RATIO = 0.6
PLACEHOLDER_CHAR_RATIO = 0.3


def _body_lines(content: str) -> list[str]:
    text = _CODE_FENCE.sub("", content)
    text = _MD_LINK.sub("", text)
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if set(line) <= _TABLE_RULE:
            continue
        lines.append(line)
    return lines


def is_unfilled_template(content: str, filename: str | None = None) -> bool:
    """True when a knowledge file is still a starter template nobody filled in."""
    stripped = content.strip()
    if not stripped:
        return True

    if filename:
        shipped = TEMPLATES_DIR / os.path.basename(filename)
        try:
            if shipped.is_file() and shipped.read_text(encoding="utf-8").strip() == stripped:
                return True
        except OSError:
            pass

    lines = _body_lines(stripped)
    if not lines:
        return True
    body = "\n".join(lines)
    placeholders = _PLACEHOLDER.findall(body)
    if len(placeholders) < MIN_PLACEHOLDERS:
        return False
    char_ratio = sum(len(p) for p in placeholders) / len(body)
    line_ratio = sum(1 for line in lines if _PLACEHOLDER.search(line)) / len(lines)
    return line_ratio >= PLACEHOLDER_LINE_RATIO or char_ratio >= PLACEHOLDER_CHAR_RATIO


def kb_files(kb_dir: str | None = None) -> list[str]:
    """Markdown files in the knowledge base that the user owns."""
    directory = kb_dir or paths["knowledge"]
    if not os.path.isdir(directory):
        return []
    return sorted(
        f for f in os.listdir(directory)
        if f.endswith(".md") and f.lower() not in IGNORED_FILES
    )


def is_empty(kb_dir: str | None = None) -> bool:
    return not kb_files(kb_dir)


def load_kb_context(files: list[tuple[str, int]], kb_dir: str | None = None) -> str:
    """Inline knowledge base files as prompt context, skipping unfilled templates."""
    directory = kb_dir or paths["knowledge"]
    kb_context = ""
    for fname, max_chars in files:
        fpath = os.path.join(directory, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read().strip()
        except OSError:
            continue
        if is_unfilled_template(content, fname):
            continue
        kb_context += f"\n--- {fname} ---\n{content[:max_chars]}\n"
    return kb_context


SETUP_HINT = (
    "Run: podcli knowledge init, then fill in the [brackets], "
    "or run /bootstrap-knowledge in your agent to draft them from an existing channel."
)

_warned = False


def warn_missing_context(task: str) -> None:
    """One stderr line per run when the knowledge base holds no usable show context."""
    global _warned
    if _warned:
        return
    _warned = True
    print(
        f"Warning: no show context in the knowledge base, {task} is running without it. {SETUP_HINT}",
        file=sys.stderr,
        flush=True,
    )
