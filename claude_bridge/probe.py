import subprocess
import re

USAGE_LIMIT_PATTERNS = [
    r"usage limit",
    r"rate.?limit",
    r"quota exceeded",
    r"exceeded.*usage",
    r"plan.*limit",
]


class ProbeError(Exception):
    pass


def probe() -> bool:
    """Return True if Claude Code usage is currently available."""
    try:
        result = subprocess.run(
            ["claude", "-p", ".", "--max-tokens", "1"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        raise ProbeError("claude probe timed out") from e
    except FileNotFoundError as e:
        raise ProbeError("claude CLI not found — is Claude Code installed?") from e

    combined = (result.stdout + result.stderr).lower()
    if any(re.search(p, combined) for p in USAGE_LIMIT_PATTERNS):
        return False
    return True
