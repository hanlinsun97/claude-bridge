from unittest.mock import patch, MagicMock
from claude_bridge.probe import probe, ProbeError

def _mock_run(stdout="hello", stderr="", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result

def test_probe_returns_true_when_output_is_normal():
    with patch("claude_bridge.probe.subprocess.run", return_value=_mock_run(stdout="ok")):
        assert probe() is True

def test_probe_returns_false_on_usage_limit():
    with patch("claude_bridge.probe.subprocess.run",
               return_value=_mock_run(stdout="", stderr="You have reached your usage limit")):
        assert probe() is False

def test_probe_returns_false_on_rate_limit():
    with patch("claude_bridge.probe.subprocess.run",
               return_value=_mock_run(stderr="rate limit exceeded")):
        assert probe() is False

def test_probe_raises_on_timeout():
    import subprocess
    with patch("claude_bridge.probe.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 30)):
        try:
            probe()
            assert False, "should have raised"
        except ProbeError:
            pass

def test_probe_raises_when_claude_not_found():
    with patch("claude_bridge.probe.subprocess.run", side_effect=FileNotFoundError()):
        try:
            probe()
            assert False, "should have raised"
        except ProbeError:
            pass
