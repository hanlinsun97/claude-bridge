from unittest.mock import patch
from claude_bridge.notify import notify


def test_notify_calls_osascript():
    with patch("claude_bridge.notify.subprocess.run") as mock_run:
        notify("hello world")
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "osascript" in args
    assert "hello world" in " ".join(args)


def test_notify_does_not_raise_on_failure():
    with patch("claude_bridge.notify.subprocess.run", side_effect=Exception("osascript missing")):
        notify("this should not raise")
