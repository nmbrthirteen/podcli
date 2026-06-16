"""Compare a candidate transcription engine against the captured baseline.

Layer-2 gate: bounds whisper.cpp (or any new engine) against openai-whisper
ground truth on text accuracy and word-timestamp drift. The acceptance bar is
deliberately forgiving because the caption pipeline already runs in production
on evenly-spaced synthetic word timings (see transcript_parser.py) — absolute
timestamp fidelity is a quality nicety, not a correctness requirement.

Usage:
    venv/bin/python3 tests/parity/compare.py <stem>
        compares baseline/<stem>/transcript.json vs candidate/<stem>/transcript.json
    venv/bin/python3 tests/parity/compare.py <baseline.json> <candidate.json>

Exit code is nonzero if any threshold is exceeded — wire it into CI as the gate
for the whisper.cpp swap.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Thresholds — tune against real fixtures, then lock in CI.
MAX_WER = float(os.environ.get("PARITY_MAX_WER", "0.08"))           # 8% word error
MAX_MEDIAN_DRIFT = float(os.environ.get("PARITY_MAX_MEDIAN_DRIFT", "0.10"))  # 100ms
MAX_P95_DRIFT = float(os.environ.get("PARITY_MAX_P95_DRIFT", "0.30"))        # 300ms


def _norm(w: str) -> str:
    return (w or "").strip().lower().strip(".,!?;:\"'")


def _words(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("words", []) if isinstance(data, dict) else data


def _wer(ref_tokens, hyp_tokens) -> float:
    """Word error rate via Levenshtein distance over token sequences."""
    n, m = len(ref_tokens), len(hyp_tokens)
    if n == 0:
        return 0.0 if m == 0 else 1.0
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref_tokens[i - 1] == hyp_tokens[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m] / n


def _drift(ref, hyp):
    """Median / p95 abs start-time diff over the order-preserving common
    subsequence of matching word tokens. Words present in only one transcript
    are ignored for drift (they're counted by WER)."""
    rt = [_norm(w.get("word", "")) for w in ref]
    ht = [_norm(w.get("word", "")) for w in hyp]
    n, m = len(rt), len(ht)
    # LCS backtrace to align matching words by position.
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if rt[i - 1] == ht[j - 1] else max(dp[i - 1][j], dp[i][j - 1])
    i, j, diffs = n, m, []
    while i > 0 and j > 0:
        if rt[i - 1] == ht[j - 1]:
            try:
                diffs.append(abs(float(ref[i - 1]["start"]) - float(hyp[j - 1]["start"])))
            except (KeyError, TypeError, ValueError):
                pass
            i, j = i - 1, j - 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    diffs.sort()
    if not diffs:
        return None, None, 0
    median = diffs[len(diffs) // 2]
    p95 = diffs[min(len(diffs) - 1, int(len(diffs) * 0.95))]
    return median, p95, len(diffs)


def compare(baseline_path: str, candidate_path: str) -> bool:
    ref, hyp = _words(baseline_path), _words(candidate_path)
    wer = _wer([_norm(w.get("word", "")) for w in ref], [_norm(w.get("word", "")) for w in hyp])
    median, p95, matched = _drift(ref, hyp)

    print(f"  baseline words: {len(ref)}   candidate words: {len(hyp)}")
    print(f"  WER:            {wer:.3f}   (max {MAX_WER})")
    if median is None:
        print("  drift:          n/a (no aligned words)")
    else:
        print(f"  drift median:   {median:.3f}s (max {MAX_MEDIAN_DRIFT})   p95: {p95:.3f}s (max {MAX_P95_DRIFT})   aligned: {matched}")

    ok = wer <= MAX_WER
    if median is not None:
        ok = ok and median <= MAX_MEDIAN_DRIFT and p95 <= MAX_P95_DRIFT
    print("  RESULT:        ", "PASS" if ok else "FAIL")
    return ok


def _resolve(arg):
    cand = os.path.join(HERE, "candidate", arg, "transcript.json")
    base = os.path.join(HERE, "baseline", arg, "transcript.json")
    if os.path.exists(base) and os.path.exists(cand):
        return base, cand
    return arg, None


def main(argv):
    if len(argv) == 2:
        base, cand = _resolve(argv[1])
        if cand is None:
            print(f"Need baseline/<stem> and candidate/<stem>, or two explicit paths.", file=sys.stderr)
            return 2
    elif len(argv) == 3:
        base, cand = argv[1], argv[2]
    else:
        print(__doc__)
        return 2
    return 0 if compare(base, cand) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
