import subprocess


def notify(message: str) -> None:
    """Send a macOS notification. Silently no-ops if osascript is unavailable."""
    script = f'display notification "{message}" with title "claude-bridge"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except Exception:
        pass
