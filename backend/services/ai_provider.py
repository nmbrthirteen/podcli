"""Single entry point for every AI generation in podcli.

Three backends, tried in order until one answers:

  cloud  podcli Pro, if signed in (fastest, no install, prompt caching)
  cli    the user's local Claude Code / Codex binary (free, needs an install)
  api    ANTHROPIC_API_KEY, called directly over HTTPS (no install, per token)

Callers pass a prompt and get text back. They do not care which backend ran,
which is the point: the local CLI is found, launched and parsed differently on
every platform, and that mess stops here.

Selection is controlled by PODCLI_AI_PROVIDER (auto|cloud|cli|api). On `auto`
the order above applies: a Pro subscriber gets what they paid for first, and the
local CLI remains the fallback if the network or the subscription is unavailable
— podcli never stops working because a server did.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services import ai_cli, podcli_cloud

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_API_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 16000


@dataclass
class AIResult:
    ok: bool
    text: str = ""
    error: str = ""
    provider: str = ""
    label: str = ""
    attempts: list[str] = field(default_factory=list)
    # Extra independent answers to the same prompt, when the backend ran several.
    # Callers that know how to merge them get a wider search for almost nothing;
    # callers that ignore them behave exactly as before.
    alternates: list[str] = field(default_factory=list)


def _mode() -> str:
    mode = (os.environ.get("PODCLI_AI_PROVIDER") or "auto").strip().lower()
    return mode if mode in ("auto", "cloud", "cli", "api") else "auto"


def _api_key() -> Optional[str]:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return key or None


def _api_model() -> str:
    return (os.environ.get("PODCLI_AI_MODEL") or "").strip() or DEFAULT_API_MODEL


def _chain() -> list[tuple[str, str, str]]:
    """Backends to try, in order: (kind, path_or_key, engine)."""
    mode = _mode()
    chain: list[tuple[str, str, str]] = []
    # A signed-in free workspace would otherwise upload the whole transcript on
    # every pass only to be told 402. Forcing the mode still tries, so someone
    # debugging entitlement can reach the server.
    if mode in ("auto", "cloud") and podcli_cloud.signed_in():
        if mode == "cloud" or podcli_cloud.entitled():
            chain.append(("cloud", "", "cloud"))
    if mode in ("auto", "cli"):
        chain.extend(("cli", path, engine) for path, engine in ai_cli._find_ai_cli_candidates())
    if mode in ("auto", "api"):
        key = _api_key()
        if key:
            chain.append(("api", key, "api"))
    return chain


def label_for(kind: str, engine: str) -> str:
    if kind == "cloud":
        return "podcli Pro"
    if kind == "api":
        return "Claude API"
    return ai_cli._engine_label(engine)


def available() -> bool:
    return bool(_chain())


def claude_cli_path() -> Optional[str]:
    """The local Claude binary, for the one caller that streams its output."""
    for kind, path, engine in _chain():
        if kind == "cli" and engine == "claude":
            return path
    return None


def status() -> dict:
    cli_status = ai_cli.get_ai_cli_status()
    chain = _chain()
    return {
        **cli_status,
        "mode": _mode(),
        "api_key_set": bool(_api_key()),
        "api_model": _api_model(),
        "available": bool(chain),
        "providers": [
            {"kind": kind, "engine": engine, "label": label_for(kind, engine)}
            for kind, _, engine in chain
        ],
    }


def extract_json(text: str) -> Optional[Any]:
    """Pull the first JSON value out of a model response.

    Models fence their JSON, prefix it with prose, or both, regardless of how
    firmly the prompt asks them not to.
    """
    if not text:
        return None
    body = text.strip()
    if "```" in body:
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", body, re.DOTALL)
        if fenced:
            body = fenced.group(1).strip()
    # By position, not by preference: a top-level array whose first element is
    # an object would otherwise match "{" at index 1 and return one element of
    # the list instead of the list.
    openers = sorted(
        (body.find(opener), opener) for opener in ("{", "[") if body.find(opener) >= 0
    )
    for start, _opener in openers:
        try:
            value, _ = json.JSONDecoder().raw_decode(body, start)
            return value
        except ValueError:
            continue
    return None


def _run_api(key: str, prompt: str, timeout: int) -> AIResult:
    payload = json.dumps({
        "model": _api_model(),
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        ANTHROPIC_URL,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        return AIResult(ok=False, provider="api", label="Claude API",
                        error=detail or f"Claude API returned HTTP {exc.code}")
    except Exception as exc:
        return AIResult(ok=False, provider="api", label="Claude API", error=str(exc))

    if body.get("stop_reason") == "refusal":
        return AIResult(ok=False, provider="api", label="Claude API",
                        error="Claude declined this request.")

    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if block.get("type") == "text"
    ).strip()
    if not text:
        return AIResult(ok=False, provider="api", label="Claude API",
                        error="Claude API returned no text.")
    return AIResult(ok=True, text=text, provider="api", label="Claude API")


def _run_cloud(purpose: str, instruction: str, system: Optional[str],
               cached_context: Optional[str], episode_source_hash: Optional[str],
               timeout: int) -> AIResult:
    try:
        payload = podcli_cloud.generate(
            purpose=purpose,
            instruction=instruction,
            system=system,
            cached_context=cached_context,
            episode_source_hash=episode_source_hash,
            timeout=timeout,
        )
    except podcli_cloud.CloudError as exc:
        return AIResult(ok=False, provider="cloud", label="podcli Pro", error=str(exc))

    text = (payload.get("text") or "").strip()
    if not text:
        return AIResult(ok=False, provider="cloud", label="podcli Pro",
                        error="podcli Pro returned no text.")
    return AIResult(
        ok=True, text=text, provider="cloud", label="podcli Pro",
        alternates=[a for a in (payload.get("alternates") or []) if a],
    )


def _run_cli(cli_path: str, engine: str, prompt: str, prompt_file: str,
             project_dir: str, timeout: int) -> AIResult:
    label = ai_cli._engine_label(engine)
    try:
        completed = ai_cli._run_ai_command(
            cli_path=cli_path,
            engine=engine,
            prompt=prompt,
            prompt_file=prompt_file,
            project_dir=project_dir,
            timeout=timeout,
        )
    except Exception as exc:
        timed_out = "timed out" in str(exc).lower() or exc.__class__.__name__ == "TimeoutExpired"
        detail = (
            f"{label} timed out ({ai_cli._format_timeout_label(timeout)} limit)"
            if timed_out else f"{label} failed to start: {exc}"
        )
        return AIResult(ok=False, provider=engine, label=label, error=detail)

    text = (completed.stdout or "").strip()
    if completed.returncode != 0 or not text:
        detail = (completed.stderr or "").strip() or text
        return AIResult(ok=False, provider=engine, label=label,
                        error=ai_cli.classify_cli_error(detail))
    return AIResult(ok=True, text=text, provider=engine, label=label)


def generate(
    prompt: str,
    *,
    timeout: int = 900,
    project_dir: Optional[str] = None,
    on_attempt: Optional[Callable[[str], None]] = None,
    accept: Optional[Callable[[str], bool]] = None,
    adapt: Optional[Callable[[str, str], str]] = None,
    purpose: str = "generate",
    stable_prefix: Optional[str] = None,
    local_prompt: Optional[str] = None,
    episode_source_hash: Optional[str] = None,
) -> AIResult:
    """Run one prompt through the first backend that answers.

    on_attempt is called with a human label ("Claude", "podcli Pro") before each
    attempt so callers can drive progress UI without knowing the chain.

    accept rejects a response the backend considers successful — an engine that
    answers with prose where JSON was asked for has failed, and the next one
    deserves a turn. Return True to accept, or False / a reason string to reject.

    adapt(engine, prompt) rewrites the prompt per backend, for engines that need
    a shorter one than the others.

    stable_prefix is the large, unchanging half of the prompt — the transcript.
    The cloud backend sends it as a separate cacheable block so it is read once
    per episode rather than once per pass, which is the difference between ~$0.41
    and ~$0.90 per episode.

    Caching wants the stable text first and the varying ask last; several local
    prompts are built the other way round, and reordering them would change what
    the free path produces. local_prompt is the escape hatch: pass the exact
    legacy string and local backends send it untouched while the cloud gets the
    split form. Omit it and the prefix is simply prepended.
    """
    chain = _chain()
    if not chain:
        return AIResult(
            ok=False,
            error="No AI available. Sign in with `podcli login`, install Claude "
                  "Code or Codex, or run `podcli env set ANTHROPIC_API_KEY sk-...`.",
        )

    if project_dir is None:
        project_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # The Claude CLI reads its prompt from a file, so an adapted prompt needs its
    # own file rather than the shared one.
    prompt_files: dict[str, str] = {}

    def prompt_file_for(text: str) -> str:
        if text not in prompt_files:
            from utils.prompt_files import write_prompt_file
            prompt_files[text] = write_prompt_file(text)
        return prompt_files[text]

    attempts: list[str] = []
    last = AIResult(ok=False, error="No AI backend produced a response.")
    try:
        for kind, target, engine in chain:
            label = label_for(kind, engine)
            if on_attempt:
                on_attempt(label)
            if kind == "cloud":
                result = _run_cloud(purpose, prompt, None, stable_prefix,
                                    episode_source_hash, timeout)
            else:
                whole = local_prompt or (
                    f"{stable_prefix}\n\n{prompt}" if stable_prefix else prompt
                )
                text = adapt(engine, whole) if adapt else whole
                if kind == "api":
                    result = _run_api(target, text, timeout)
                else:
                    result = _run_cli(target, engine, text, prompt_file_for(text),
                                      project_dir, timeout)
            if result.ok and accept:
                verdict = accept(result.text)
                if verdict is not True:
                    reason = verdict if isinstance(verdict, str) and verdict else \
                        f"{label} returned an unusable response."
                    result = AIResult(ok=False, provider=result.provider,
                                      label=label, error=reason)
            attempts.append(f"{label}: {'ok' if result.ok else result.error}")
            if result.ok:
                result.attempts = attempts
                return result
            last = result
        last.attempts = attempts
        return last
    finally:
        for path in prompt_files.values():
            try:
                os.unlink(path)
            except OSError:
                pass


def generate_json(prompt: str, **kwargs) -> tuple[Optional[Any], AIResult]:
    """generate() plus the fence-stripping every caller was doing by hand.

    A backend whose answer will not parse is treated as failed, so the next one
    in the chain gets a turn.
    """
    kwargs.setdefault("accept", lambda text: extract_json(text) is not None)
    result = generate(prompt, **kwargs)
    if not result.ok:
        return None, result
    return extract_json(result.text), result
