import subprocess


def notify(message: str) -> None:
    """Send a macOS notification. Silently no-ops if osascript is unavailable."""
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe}" with title "claude-bridge"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except Exception:
        pass
