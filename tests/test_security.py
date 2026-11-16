import re
import subprocess
from pathlib import Path

import pytest

from backend import security
from backend.tools import registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("ip,expected", [
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.5.5", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("::1", False),
    ("fe80::1", False), ("::ffff:127.0.0.1", False), ("224.0.0.1", False),
    ("8.8.8.8", True), ("1.1.1.1", True),
])
def test_is_public_ip(ip, expected):
    assert security.is_public_ip(ip) is expected


@pytest.mark.parametrize("url", [
    "http://localhost/", "http://localhost:8000/", "https://127.0.0.1/", "http://10.0.0.5/admin",
    "http://192.168.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
    "http://0.0.0.0/", "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)",
    "http://user:pass@8.8.8.8/", "http://8.8.8.8:22/", "", "   ", "http://", "not a url",
    "http://printer.local/", "http://foo.internal/", "http://8.8.8.8/\nHost: evil",
])
def test_validate_url_rejects(url):
    with pytest.raises(security.UrlRejected):
        security.validate_url(url)


def test_validate_url_accepts_public_ip_and_strips_fragment():
    assert security.validate_url("https://8.8.8.8/path#frag") == "https://8.8.8.8/path"


def test_redirect_to_private_address_is_blocked(monkeypatch):
    class FakeResp:
        status_code = 302
        headers = {"Location": "http://127.0.0.1:8000/admin"}
        def close(self): pass
    monkeypatch.setattr(security.requests.Session, "get", lambda self, *a, **k: FakeResp())
    with pytest.raises(security.UrlRejected):
        security.safe_get("http://8.8.8.8/")


def test_registry_rejects_unapproved_tool_and_flags():
    with pytest.raises(registry.ToolNotApproved):
        registry.build_command("bash", {})
    with pytest.raises(registry.ToolNotApproved):
        registry.build_command("rm", {"-rf": "/"})
    with pytest.raises(registry.ToolNotApproved):
        registry.build_command("site_discovery", {"--exec": "id"})


def test_build_command_is_argv_list_without_shell():
    cmd = registry.build_command("site_discovery", {"--url": "https://8.8.8.8; rm -rf /", "--max-pages": 2})
    assert isinstance(cmd, list) and all(isinstance(c, str) for c in cmd)
    assert cmd[-3] == "https://8.8.8.8; rm -rf /"  # stays a single inert argument


def test_no_shell_execution_anywhere_in_source():
    pattern = re.compile(r"shell\s*=\s*True|os\.system|os\.popen|\beval\(|\bexec\(")
    offenders = []
    for f in (ROOT / "backend").rglob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if pattern.search(line) and not line.strip().startswith("#"):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_tool_cli_rejects_localhost_with_exit_code_2():
    r = subprocess.run(["python3", "-B", str(ROOT / "backend/tools/site_discovery.py"), "--url", "http://localhost:8000"],
                       capture_output=True, text=True, cwd=ROOT, shell=False)
    assert r.returncode == 2
    assert "not allowed" in r.stdout or "blocked" in r.stdout


def test_output_paths_are_confined_to_outputs_dir():
    from backend.utils.helpers import safe_path
    with pytest.raises(ValueError):
        safe_path("/etc/passwd")
    with pytest.raises(ValueError):
        safe_path(str(ROOT / "outputs" / ".." / "backend" / "main.py"))
