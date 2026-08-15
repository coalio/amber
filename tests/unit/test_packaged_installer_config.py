from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class _SetupProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self._send_json({"ok": True, "runner": "codex-cli", "yolo_mode": True})

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        self._send_json({"errors": [{"message": "synthetic stop after packaged config reload"}]})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_packaged_installer_configure_preserves_nested_linear_defaults(tmp_path: Path) -> None:
    # build the same release archive shape consumed by the public installer
    build_env = os.environ.copy()
    build_env["PYTHON"] = sys.executable
    build = subprocess.run(
        ["bash", "scripts/build_release.sh"],
        cwd=ROOT,
        env=build_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    # install into an isolated Amber home with deterministic host prerequisites
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_podman(fake_bin / "podman")
    _write_executable(fake_bin / "slirp4netns", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${LD_LIBRARY_PATH:-<unset>}" > "$AMBER_HOST_ENV_LOG"
exit 0
""",
    )

    amber_home = tmp_path / ".amber"
    archive = ROOT / "dist" / "amber-linux-x86_64.tar.gz"
    with tarfile.open(archive, "r:gz") as packaged:
        names = [name.removeprefix("./") for name in packaged.getnames()]
    forbidden_roots = {"torch", "transformers", "nvidia", "triton", "cuda"}
    assert not any(name.split("/", 1)[0] in forbidden_roots for name in names)
    assert "resources/ml/attention_worker.py" in names
    assert "resources/ml/requirements.txt" in names

    env = os.environ.copy()
    env.update(
        {
            "AMBER_HOME": str(amber_home),
            "AMBER_RELEASE_ARCHIVE": str(archive),
            "AMBER_RELEASE_TAG": "package-test",
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    install = subprocess.run(
        ["bash", "installer/install.sh", "package-test", "--headless"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    # let Codex preflight pass, then stop at Linear after the packaged config reload
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SetupProbeHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        config_path = amber_home / "workspaces" / "package-test" / "config.toml"
        config_text = config_path.read_text(encoding="utf-8")
        config_text = re.sub(
            r'app_server_url = "[^"]+"',
            f'app_server_url = "http://127.0.0.1:{server.server_port}"',
            config_text,
        )
        config_text = re.sub(r"app_server_port = \d+", f"app_server_port = {server.server_port}", config_text)
        config_path.write_text(config_text, encoding="utf-8")

        configure_env = env.copy()
        configure_env.update(
            {
                "AMBER_AI_API_KEY": "test-openai-key",
                "AMBER_AI_MODEL": "gpt-test",
                "API_ID": "10001",
                "API_HASH": "test-telegram-api-hash",
                "AMBER_LINEAR_API_KEY": "test-linear-key",
                "AMBER_LINEAR_API_URL": f"http://127.0.0.1:{server.server_port}/graphql",
                "AMBER_CODEX_MODEL": "gpt-test",
                "AMBER_CODEX_REASONING_EFFORT": "high",
                "AMBER_CODEX_AUTO_UPDATE": "false",
            }
        )
        configure = subprocess.run(
            [str(amber_home / "bin" / "amber"), "workspace", "configure", "package-test", "--headless"],
            cwd=tmp_path,
            env=configure_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    # prove setup passed the former failure point and retained the shipped tables
    assert configure.returncode != 0
    assert "synthetic stop after packaged config reload" in configure.stderr
    assert "linear.project.statuses" not in configure.stderr

    with config_path.open("rb") as handle:
        saved_config = tomllib.load(handle)
    assert saved_config["linear"]["project"]["statuses"] == {
        "planned": ["Planned"],
        "started": ["Started"],
        "completed": ["Completed"],
        "canceled": ["Canceled"],
    }

    # PyInstaller may prepend bundled libraries, but host programs must see the
    # loader path that existed before the frozen process started.
    host_env_log = tmp_path / "host-env.log"
    service_env = env.copy()
    service_env.update(
        {
            "AMBER_HOST_ENV_LOG": str(host_env_log),
            "LD_LIBRARY_PATH": "/synthetic/host/lib",
        }
    )
    service = subprocess.run(
        [str(amber_home / "bin" / "amber"), "service", "status", "--workspace", "package-test"],
        cwd=tmp_path,
        env=service_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    assert service.returncode == 0, service.stdout + service.stderr
    assert host_env_log.read_text(encoding="utf-8").strip() == "/synthetic/host/lib"


def _write_fake_podman(path: Path) -> None:
    _write_executable(
        path,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "info" && "${2:-}" == "--debug" ]]; then
  printf '%s\n' 'host:' '  security:' '    rootless: true' '  cgroupVersion: v2'
  exit 0
fi
if [[ "${1:-}" == "run" && "${2:-}" == "--help" ]]; then
  printf '%s\n' '--userns --network --cgroups --memory --cpus --pids-limit'
  exit 0
fi
if [[ "${1:-}" == "container" && "${2:-}" == "exists" ]]; then
  exit 1
fi
if [[ "${1:-}" == "inspect" ]]; then
  printf '%s\n' 'amber-codex-sandbox:ubuntu-24.04-codex-cli'
  exit 0
fi
exit 0
""",
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
