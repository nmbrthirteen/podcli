"""Discovery and invocation of the user's local AI CLI (Claude Code or Codex).

Finding the binary is genuinely hard: npm prefixes, version managers, shell
aliases and platform extensions all move it. That search lives here so the
provider layer above can treat "run this prompt" as one call.
"""

import os
import subprocess
import sys
from functools import lru_cache
from typing import Optional

def _cli_name_exts() -> list[str]:
    if sys.platform == "win32":
        return ["", ".cmd", ".exe", ".bat"]
    return [""]


def _resolve_cli_path(path: str) -> Optional[str]:
    for ext in _cli_name_exts():
        candidate = path + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def _dedupe_dirs(dirs: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for directory in dirs:
        if not directory:
            continue
        directory = os.path.expanduser(directory)
        if directory in seen:
            continue
        seen.add(directory)
        if os.path.isdir(directory):
            ordered.append(directory)
    return ordered


def _npmrc_prefix_dirs() -> list[str]:
    dirs: list[str] = []
    npmrc_paths = [os.path.join(os.path.expanduser("~"), ".npmrc")]
    try:
        from services.env_settings import _env_path
        npmrc_paths.append(os.path.join(os.path.dirname(_env_path()), ".npmrc"))
    except Exception:
        pass
    for npmrc in npmrc_paths:
        if not os.path.isfile(npmrc):
            continue
        try:
            with open(npmrc, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                        continue
                    if stripped.startswith("prefix="):
                        prefix = stripped.split("=", 1)[1].strip()
                        if prefix:
                            dirs.append(prefix if sys.platform == "win32" else os.path.join(prefix, "bin"))
        except Exception:
            pass
    return dirs


def _package_manager_bin_dirs() -> list[str]:
    dirs: list[str] = []
    npm_cmds = [
        (["npm", "config", "get", "prefix"], "prefix"),
        (["npm", "root", "-g"], "root"),
    ]
    for args, kind in npm_cmds:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        raw = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if not raw:
            continue
        if kind == "prefix":
            dirs.append(raw if sys.platform == "win32" else os.path.join(raw, "bin"))
        elif kind == "root":
            dirs.append(os.path.join(raw, ".bin"))
        else:
            dirs.append(raw)

    for args, kind in (
        (["pnpm", "config", "get", "global-bin-dir"], "bin"),
        (["pnpm", "bin", "-g"], "bin"),
        (["yarn", "global", "bin"], "bin"),
    ):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        raw = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if raw:
            dirs.append(raw)

    return dirs


def _version_manager_bin_dirs() -> list[str]:
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, "bin"),
        os.path.join(home, ".asdf", "shims"),
        os.path.join(home, ".local", "share", "mise", "shims"),
        os.path.join(home, ".local", "share", "rtx", "shims"),
        os.path.join(home, ".bun", "bin"),
        os.path.join(home, ".cargo", "bin"),
        os.path.join(home, "go", "bin"),
        os.path.join(home, ".local", "share", "pnpm"),
        os.path.join(home, ".claude", "bin"),
    ]

    nvm_dir = os.environ.get("NVM_DIR") or os.path.join(home, ".nvm")
    try:
        import glob
        dirs.extend(sorted(glob.glob(os.path.join(nvm_dir, "versions", "node", "*", "bin")), reverse=True))
        dirs.extend(glob.glob(os.path.join(home, ".fnm", "node-versions", "*", "installation", "bin")))
        dirs.extend(glob.glob(os.path.join(home, ".local", "share", "fnm", "node-versions", "*", "installation", "bin")))
    except Exception:
        pass

    fnm_bin = os.path.join(home, ".local", "share", "fnm", "current", "bin")
    dirs.append(fnm_bin)
    dirs.append(os.path.join(home, ".volta", "bin"))

    if sys.platform == "win32":
        for env_key in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(env_key)
            if not base:
                continue
            dirs.extend([
                os.path.join(base, "npm"),
                os.path.join(base, "Programs", "nodejs"),
                os.path.join(base, "Microsoft", "WinGet", "Links"),
            ])
        dirs.append(os.path.join(home, "scoop", "shims"))
        dirs.append(os.path.join(os.environ.get("ProgramData", ""), "npm"))
    else:
        dirs.extend([
            "/usr/bin",
            "/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/snap/bin",
            "/var/lib/snapd/snap/bin",
        ])

    npm_prefix = (
        os.environ.get("NPM_CONFIG_PREFIX")
        or os.environ.get("npm_config_prefix")
        or ""
    ).strip()
    if npm_prefix:
        dirs.append(os.path.join(os.path.expanduser(npm_prefix), "bin"))

    return dirs


def _static_lookup_dirs() -> list[str]:
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".claude", "local", "bin"),
        os.path.join(home, ".claude", "local", "node_modules", ".bin"),
        os.path.join(home, ".npm-global", "bin"),
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(os.path.join(appdata, "npm"))
        dirs.append(os.path.join(home, ".local", "bin"))
    return dirs


@lru_cache(maxsize=8)
def _lookup_dirs(_key: tuple) -> list[str]:
    return _dedupe_dirs(
        _static_lookup_dirs()
        + _version_manager_bin_dirs()
        + _npmrc_prefix_dirs()
        + _package_manager_bin_dirs()
    )


def _all_lookup_dirs() -> list[str]:
    return list(_lookup_dirs(_discovery_key()))


def _path_lookup_dirs() -> list[str]:
    return _all_lookup_dirs()


def _npm_global_bin_dirs() -> list[str]:
    return _package_manager_bin_dirs()


def _parse_shell_lookup_line(line: str) -> Optional[str]:
    candidate = line.strip().strip('"')
    if not candidate:
        return None
    if " is " in candidate:
        candidate = candidate.split(" is ", 1)[1].strip()
    if candidate.startswith("(") and candidate.endswith(")"):
        candidate = candidate[1:-1].strip()
    return _resolve_cli_path(candidate) or (candidate if os.path.isfile(candidate) else None)


def _shell_lookup(name: str) -> Optional[str]:
    if sys.platform == "win32":
        commands = [
            ["where", name],
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Command {name} -All -ErrorAction SilentlyContinue | "
                f"Select-Object -ExpandProperty Source)",
            ],
        ]
    else:
        commands = [
            ["sh", "-lc", f"command -v {name}"],
            ["bash", "-lc", f"type -a {name} 2>/dev/null"],
            ["zsh", "-lc", f"whence -p {name} 2>/dev/null; command -v {name} 2>/dev/null"],
            ["fish", "-lc", f"type -a {name} 2>/dev/null"],
        ]

    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except Exception:
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue
        for line in result.stdout.strip().splitlines():
            resolved = _parse_shell_lookup_line(line)
            if resolved:
                return resolved
    return None


def _glob_cli_paths(name: str) -> list[str]:
    import glob
    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, ".claude", "bin", name),
        os.path.join(home, ".claude", "*", "bin", name),
        os.path.join(home, ".local", "share", "claude", "bin", name),
        os.path.join(home, ".local", "share", "npm", "*", "bin", name),
    ]
    if sys.platform == "win32":
        patterns.extend([
            os.path.join(home, ".claude", "bin", f"{name}.exe"),
            os.path.join(home, ".claude", "bin", f"{name}.cmd"),
        ])
    found: list[str] = []
    for pattern in patterns:
        try:
            found.extend(glob.glob(pattern))
        except Exception:
            pass
    return found


def _configured_cli_path(engine: str) -> Optional[str]:
    env_key = "PODCLI_CLAUDE_PATH" if engine == "claude" else "PODCLI_CODEX_PATH"
    raw = (os.environ.get(env_key) or "").strip()
    if not raw:
        try:
            from services.env_settings import _read_pairs
            raw = (_read_pairs().get(env_key) or "").strip()
        except Exception:
            pass
    if not raw:
        return None
    return _resolve_cli_path(raw) or (raw if os.path.isfile(raw) else None)


def _find_cli(name: str, extra_paths: list[str] = None) -> Optional[str]:
    import shutil

    for path in (extra_paths or []) + _glob_cli_paths(name):
        resolved = _resolve_cli_path(path)
        if resolved:
            return resolved

    lookup_dirs = _all_lookup_dirs()
    lookup_path = os.pathsep.join(lookup_dirs + [os.environ.get("PATH", "")])
    found = shutil.which(name, path=lookup_path)
    if found:
        return found

    for directory in lookup_dirs:
        resolved = _resolve_cli_path(os.path.join(directory, name))
        if resolved:
            return resolved

    for directory in (os.environ.get("PATH", "") or "").split(os.pathsep):
        if not directory:
            continue
        resolved = _resolve_cli_path(os.path.join(directory, name))
        if resolved:
            return resolved

    return _shell_lookup(name)


def _ai_cli_search_paths(name: str) -> list[str]:
    paths_out = [os.path.join(directory, name) for directory in _all_lookup_dirs()]
    paths_out.extend(_glob_cli_paths(name))
    return paths_out


def _env_cli_path(engine: str) -> Optional[str]:
    return _configured_cli_path(engine)


def get_ai_cli_status() -> dict:
    configured = {
        "claude": _configured_cli_path("claude"),
        "codex": _configured_cli_path("codex"),
    }
    candidates = [
        {"engine": engine, "path": path}
        for path, engine in _find_ai_cli_candidates()
    ]
    return {
        "configured": configured,
        "candidates": candidates,
        "available": bool(candidates),
        "searched_dirs": _all_lookup_dirs(),
    }


def _env_file_stamp() -> tuple:
    """
    Identity of the .env discovery also reads.

    A configured CLI path can come from the file as well as the environment,
    and the backend task runner is long-lived: it serves the request that saves
    the path and every request after it. Without the file in the key, saving a
    path in the studio has no effect until the process restarts, which is a
    regression against the old probe-every-time behaviour.
    """
    try:
        from services.env_settings import _env_path
        path = _env_path()
        stat = os.stat(path)
        return (path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        # No file, or no reading it: nothing to invalidate against.
        return ()


def _discovery_key() -> tuple:
    """Everything discovery reads. Changing any of it must re-probe."""
    return tuple(
        os.environ.get(name, "")
        for name in (
            "PATH", "HOME", "NVM_DIR", "APPDATA", "ProgramData",
            "NPM_CONFIG_PREFIX", "npm_config_prefix",
            "PODCLI_CLAUDE_PATH", "PODCLI_CODEX_PATH",
        )
    ) + _env_file_stamp()


@lru_cache(maxsize=8)
def _discover(_key: tuple) -> list[tuple[str, str]]:
    candidates = []

    claude = _env_cli_path("claude") or _find_cli("claude", _ai_cli_search_paths("claude"))
    if claude:
        candidates.append((claude, "claude"))

    codex = _env_cli_path("codex") or _find_cli("codex", _ai_cli_search_paths("codex"))
    if codex:
        candidates.append((codex, "codex"))

    return candidates


def _find_ai_cli_candidates() -> list[tuple[str, str]]:
    # Each probe shells out to npm, pnpm and yarn, which costs ~3s. Callers ask
    # several times per render and the filesystem does not move underneath them,
    # so the result is cached against the environment it was derived from.
    return list(_discover(_discovery_key()))


def _find_ai_cli() -> tuple[Optional[str], str]:
    """
    Find the best available AI CLI.

    Returns (path, engine) where engine is "claude" or "codex".
    Returns (None, "") if neither is available.
    """
    candidates = _find_ai_cli_candidates()
    return candidates[0] if candidates else (None, "")


def _engine_label(engine: str) -> str:
    """Human-readable name for an AI engine id."""
    if engine == "claude":
        return "Claude"
    if engine == "codex":
        return "Codex"
    return "AI"


def _format_timeout_label(timeout: int) -> str:
    """Render a human-readable timeout label for progress messages."""
    if timeout % 60 == 0 and timeout >= 60:
        minutes = timeout // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"
    return f"{timeout}s"


def _run_ai_command(
    cli_path: str,
    engine: str,
    prompt: str,
    prompt_file: str,
    project_dir: str,
    timeout: int,
) -> subprocess.CompletedProcess:
    """Execute one AI CLI prompt and return the completed process."""
    if engine == "codex":
        output_file = prompt_file + ".out"
        result = subprocess.run(
            [
                cli_path, "exec",
                "--full-auto",
                "-o", output_file,
                prompt,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=project_dir,
            timeout=timeout,
        )
        if os.path.exists(output_file):
            with open(output_file, encoding="utf-8") as f:
                result = subprocess.CompletedProcess(
                    args=result.args,
                    returncode=result.returncode,
                    stdout=f.read(),
                    stderr=result.stderr,
                )
            try:
                os.unlink(output_file)
            except Exception:
                pass
        return result

    shell = sys.platform == "win32" and cli_path.lower().endswith((".cmd", ".bat"))
    cmd = f'"{cli_path}" --print -p -' if shell else [cli_path, "--print", "-p", "-"]
    with open(prompt_file, encoding="utf-8") as prompt_fh:
        return subprocess.run(
            cmd,
            stdin=prompt_fh,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=project_dir,
            timeout=timeout,
            shell=shell,
        )


def classify_cli_error(detail: str) -> str:
    """Turn a raw AI CLI failure into an actionable hint. The generic
    'check login' message hides whether it's auth, a plan limit, or a crash."""
    low = (detail or "").lower()
    if any(s in low for s in ("not logged in", "please run", "/login", "authenticate", "unauthorized", "invalid api key", "no credentials")):
        return "not logged in. Run `claude` (or `codex`) once in a terminal to authenticate, then retry."
    if any(s in low for s in ("usage limit", "rate limit", "quota", "too many requests", "429")):
        return "usage or rate limit reached on your plan. Wait for the limit to reset, then retry."
    if "timed out" in low or "timeout" in low:
        return detail
    if not detail:
        return "the AI CLI returned no output. Run `claude` once in a terminal to confirm it responds."
    return detail
