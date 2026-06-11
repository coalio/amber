from __future__ import annotations

import os
import stat
import subprocess
import tarfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_installer_downloads_split_release_without_full_asset(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)
    payload = archive.read_bytes()
    part_a = tmp_path / "amber-linux-x86_64.tar.gz.part-aa"
    part_b = tmp_path / "amber-linux-x86_64.tar.gz.part-ab"
    split_at = max(1, len(payload) // 2)
    part_a.write_bytes(payload[:split_at])
    part_b.write_bytes(payload[split_at:])

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        out=""
        url=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o)
              out="$2"
              shift 2
              ;;
            *)
              url="$1"
              shift
              ;;
          esac
        done

        case "$url" in
          *api.github.com*/releases/latest)
            cat <<'JSON'
        {
          "tag_name": "v0.1.0",
          "assets": [
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64.tar.gz.part-ab"},
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64.tar.gz.part-aa"}
          ]
        }
        JSON
            ;;
          *amber-linux-x86_64.tar.gz.part-aa)
            cp "$FAKE_PART_A" "$out"
            ;;
          *amber-linux-x86_64.tar.gz.part-ab)
            cp "$FAKE_PART_B" "$out"
            ;;
          *)
            echo "unexpected curl url: $url" >&2
            exit 2
            ;;
        esac
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    tmp_root = tmp_path / "tmp-empty"
    tmp_root.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(tmp_path / ".amber"),
            "AMBER_TTY": str(tty),
            "FAKE_PART_A": str(part_a),
            "FAKE_PART_B": str(part_b),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TMPDIR": str(tmp_root),
        }
    )
    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Downloading split Amber v0.1.0 assets from coalio/amber..." in result.stdout
    assert "Next steps" in result.stdout
    assert "workspace doctor indiedreamers --external --service" in result.stdout
    assert "Happy hacking." in result.stdout
    assert (tmp_path / ".amber" / "releases" / "v0.1.0" / "amber").exists()
    assert (tmp_path / ".amber" / "bin" / "amber").resolve() == tmp_path / ".amber" / "releases" / "v0.1.0" / "amber"
    assert fake_log.read_text(encoding="utf-8").splitlines() == [
        "workspace init indiedreamers",
        "workspace configure indiedreamers",
    ]
    assert "codex.progress" not in result.stderr


def test_installer_suppresses_amber_json_logs_when_not_verbose(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    tty = tmp_path / "tty"
    tty.write_text("", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_JSON_LOG": "1",
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_RELEASE_ARCHIVE": str(archive),
            "AMBER_RELEASE_TAG": "local",
            "AMBER_TTY": str(tty),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "codex.progress" not in result.stdout
    assert "codex.progress" not in result.stderr


def test_installer_can_install_full_modernbert_release(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    curl_log = tmp_path / "curl.log"
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        out=""
        url=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o)
              out="$2"
              shift 2
              ;;
            *)
              url="$1"
              shift
              ;;
          esac
        done

        printf '%s\\n' "$url" >> "$FAKE_CURL_LOG"
        case "$url" in
          *api.github.com*/releases/latest)
            cat <<'JSON'
        {
          "tag_name": "v0.5.0",
          "assets": [
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64.tar.gz"},
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64-full.tar.gz"}
          ]
        }
        JSON
            ;;
          *amber-linux-x86_64-full.tar.gz)
            cp "$FAKE_ARCHIVE" "$out"
            ;;
          *amber-linux-x86_64.tar.gz)
            echo "standard package should not have been downloaded" >&2
            exit 3
            ;;
          *)
            echo "unexpected curl url: $url" >&2
            exit 2
            ;;
        esac
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("\ny\n\n\n", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"
    tmp_root = tmp_path / "tmp-empty"
    tmp_root.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_TTY": str(tty),
            "FAKE_ARCHIVE": str(archive),
            "FAKE_CURL_LOG": str(curl_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TMPDIR": str(tmp_root),
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Using full Amber package with local ModernBERT scorer: amber-linux-x86_64-full.tar.gz" in result.stdout
    assert "Enabling local ModernBERT attention scorer for this workspace..." in result.stdout
    assert (amber_home / "packages" / "v0.5.0" / "amber-linux-x86_64-full.tar.gz").exists()
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://api.github.com/repos/coalio/amber/releases/latest",
        "https://downloads.example/amber-linux-x86_64-full.tar.gz",
    ]
    config = (amber_home / "workspaces" / "indiedreamers" / "config.toml").read_text(encoding="utf-8")
    assert 'scorer = "modernbert"' in config


def test_installer_reuses_cached_release_archive(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    curl_log = tmp_path / "curl.log"
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        out=""
        url=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o)
              out="$2"
              shift 2
              ;;
            *)
              url="$1"
              shift
              ;;
          esac
        done

        printf '%s\\n' "$url" >> "$FAKE_CURL_LOG"
        case "$url" in
          *api.github.com*/releases/latest)
            cat <<'JSON'
        {
          "tag_name": "v0.2.0",
          "assets": [
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64.tar.gz"}
          ]
        }
        JSON
            ;;
          *amber-linux-x86_64.tar.gz)
            cp "$FAKE_ARCHIVE" "$out"
            ;;
          *)
            echo "unexpected curl url: $url" >&2
            exit 2
            ;;
        esac
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"
    tmp_root = tmp_path / "tmp-empty"
    tmp_root.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_TTY": str(tty),
            "FAKE_ARCHIVE": str(archive),
            "FAKE_CURL_LOG": str(curl_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TMPDIR": str(tmp_root),
        }
    )

    first = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    second = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Downloading Amber v0.2.0 from coalio/amber..." in first.stdout
    assert "Using cached Amber v0.2.0 package" in second.stdout
    assert "Extracting Amber v0.2.0..." in second.stdout
    assert (amber_home / "packages" / "v0.2.0" / "amber-linux-x86_64.tar.gz").exists()
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://api.github.com/repos/coalio/amber/releases/latest",
        "https://downloads.example/amber-linux-x86_64.tar.gz",
        "https://api.github.com/repos/coalio/amber/releases/latest",
    ]


def test_installer_downloads_fresh_when_user_declines_cached_archive(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    curl_log = tmp_path / "curl.log"
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        out=""
        url=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o)
              out="$2"
              shift 2
              ;;
            *)
              url="$1"
              shift
              ;;
          esac
        done

        printf '%s\\n' "$url" >> "$FAKE_CURL_LOG"
        case "$url" in
          *api.github.com*/releases/latest)
            cat <<'JSON'
        {
          "tag_name": "v0.4.0",
          "assets": [
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64.tar.gz"}
          ]
        }
        JSON
            ;;
          *amber-linux-x86_64.tar.gz)
            cp "$FAKE_ARCHIVE" "$out"
            ;;
          *)
            echo "unexpected curl url: $url" >&2
            exit 2
            ;;
        esac
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("\nn\nn\n\n", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"
    tmp_root = tmp_path / "tmp-empty"
    tmp_root.mkdir()
    cached_archive = amber_home / "packages" / "v0.4.0" / "amber-linux-x86_64.tar.gz"
    cached_archive.parent.mkdir(parents=True)
    cached_archive.write_bytes(archive.read_bytes())

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_TTY": str(tty),
            "FAKE_ARCHIVE": str(archive),
            "FAKE_CURL_LOG": str(curl_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TMPDIR": str(tmp_root),
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Downloading a fresh Amber v0.4.0 package..." in result.stdout
    assert "Using cached Amber v0.4.0 package" not in result.stdout
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://api.github.com/repos/coalio/amber/releases/latest",
        "https://downloads.example/amber-linux-x86_64.tar.gz",
    ]


def test_installer_recovers_tmp_release_archive_before_download(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)
    tmp_root = tmp_path / "tmp-root"
    recovered_dir = tmp_root / "tmp.previous"
    recovered_dir.mkdir(parents=True)
    recovered_archive = recovered_dir / "amber-linux-x86_64.tar.gz"
    recovered_archive.write_bytes(archive.read_bytes())

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    curl_log = tmp_path / "curl.log"
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        out=""
        url=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o)
              out="$2"
              shift 2
              ;;
            *)
              url="$1"
              shift
              ;;
          esac
        done

        printf '%s\\n' "$url" >> "$FAKE_CURL_LOG"
        case "$url" in
          *api.github.com*/releases/latest)
            cat <<'JSON'
        {
          "tag_name": "v0.3.0",
          "assets": [
            {"browser_download_url": "https://downloads.example/amber-linux-x86_64.tar.gz"}
          ]
        }
        JSON
            ;;
          *amber-linux-x86_64.tar.gz)
            echo "package download should have been skipped" >&2
            exit 3
            ;;
          *)
            echo "unexpected curl url: $url" >&2
            exit 2
            ;;
        esac
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_TTY": str(tty),
            "FAKE_CURL_LOG": str(curl_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TMPDIR": str(tmp_root),
        }
    )
    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Recovering downloaded Amber v0.3.0 package from" in result.stdout
    assert "Caching Amber package..." in result.stdout
    assert "Extracting Amber v0.3.0..." in result.stdout
    assert (amber_home / "packages" / "v0.3.0" / "amber-linux-x86_64.tar.gz").exists()
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://api.github.com/repos/coalio/amber/releases/latest",
    ]


def test_installer_applies_codex_cgroup_fallback_when_cgroupfs_probe_passes(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "podman",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "info" && "${2:-}" == "--debug" ]]; then
          cat <<'INFO'
        host:
          cgroupManager: systemd
          cgroupVersion: v2
          kernel: 6.6.87.2-microsoft-standard-WSL2
          security:
            rootless: true
        INFO
          exit 0
        fi
        if [[ "${1:-}" == "run" && "${2:-}" == "--help" ]]; then
          printf '%s\\n' '--userns --network --memory --cpus --pids-limit'
          exit 0
        fi
        if [[ "${1:-}" == "image" && "${2:-}" == "exists" ]]; then
          exit 0
        fi
        if [[ "${1:-}" == "--cgroup-manager=cgroupfs" && "${2:-}" == "run" ]]; then
          exit 0
        fi
        if [[ "${1:-}" == "run" ]]; then
          echo "Error: Interactive authentication required" >&2
          exit 125
        fi
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "slirp4netns",
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("y\ny\n\n\n", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_RELEASE_ARCHIVE": str(archive),
            "AMBER_RELEASE_TAG": "local",
            "AMBER_TTY": str(tty),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Podman fallback probe passed with cgroupfs and Codex resource limits disabled" in result.stdout
    assert "Applying Codex Podman workspace settings..." in result.stdout
    config = (amber_home / "workspaces" / "indiedreamers" / "config.toml").read_text(encoding="utf-8")
    assert 'podman_cgroup_manager = "cgroupfs"' in config
    assert "enforce_resource_limits = false" in config


def test_installer_can_disable_codex_resource_limits_by_choice(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _add_fake_installer_prereqs(fake_bin)
    tty = tmp_path / "tty"
    tty.write_text("n\n\n\n", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_RELEASE_ARCHIVE": str(archive),
            "AMBER_RELEASE_TAG": "local",
            "AMBER_TTY": str(tty),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Codex resource limits will be disabled for this workspace." in result.stdout
    config = (amber_home / "workspaces" / "indiedreamers" / "config.toml").read_text(encoding="utf-8")
    assert 'podman_cgroup_manager = "none"' in config
    assert "enforce_resource_limits = false" in config


def test_installer_auto_applies_cgroupfs_when_disabled_limits_probe_needs_it(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "podman",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "info" && "${2:-}" == "--debug" ]]; then
          cat <<'INFO'
        host:
          security:
            rootless: true
          cgroupVersion: v2
        INFO
          exit 0
        fi
        if [[ "${1:-}" == "run" && "${2:-}" == "--help" ]]; then
          printf '%s\\n' '--userns --network --cgroups --memory --cpus --pids-limit'
          exit 0
        fi
        if [[ "${1:-}" == "image" && "${2:-}" == "exists" ]]; then
          exit 0
        fi
        if [[ "${1:-}" == "--cgroup-manager=cgroupfs" && "${2:-}" == "run" ]]; then
          exit 0
        fi
        if [[ "${1:-}" == "run" ]]; then
          echo "Error: Interactive authentication required" >&2
          exit 125
        fi
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "slirp4netns",
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("n\n\n\n", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_RELEASE_ARCHIVE": str(archive),
            "AMBER_RELEASE_TAG": "local",
            "AMBER_TTY": str(tty),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Codex resource limits will be disabled for this workspace." in result.stdout
    assert "Trying Amber's workspace-only fallback with cgroupfs and Codex resource limits disabled..." in result.stdout
    assert "Try Amber's workspace-only fallback with cgroupfs and no Codex resource limits?" not in result.stdout
    assert "Error: Interactive authentication required" not in result.stderr
    assert "Run the installer with -v for the full Podman error." not in result.stderr
    config = (amber_home / "workspaces" / "indiedreamers" / "config.toml").read_text(encoding="utf-8")
    assert 'podman_cgroup_manager = "cgroupfs"' in config
    assert "enforce_resource_limits = false" in config


def test_installer_verbose_flag_prints_full_podman_probe_error(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "podman",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "info" && "${2:-}" == "--debug" ]]; then
          cat <<'INFO'
        host:
          security:
            rootless: true
          cgroupVersion: v2
        INFO
          exit 0
        fi
        if [[ "${1:-}" == "run" && "${2:-}" == "--help" ]]; then
          printf '%s\\n' '--userns --network --cgroups --memory --cpus --pids-limit'
          exit 0
        fi
        if [[ "${1:-}" == "image" && "${2:-}" == "exists" ]]; then
          exit 0
        fi
        if [[ "${1:-}" == "--cgroup-manager=cgroupfs" && "${2:-}" == "run" ]]; then
          exit 0
        fi
        if [[ "${1:-}" == "run" ]]; then
          echo "Error: verbose podman probe detail" >&2
          exit 125
        fi
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "slirp4netns",
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
    tty = tmp_path / "tty"
    tty.write_text("n\n\n\n", encoding="utf-8")
    fake_log = tmp_path / "amber.log"
    amber_home = tmp_path / ".amber"

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_RELEASE_ARCHIVE": str(archive),
            "AMBER_RELEASE_TAG": "local",
            "AMBER_TTY": str(tty),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "-v", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Full Podman probe error:" in result.stderr
    assert "Error: verbose podman probe detail" in result.stderr
    assert "Run the installer with -v for the full Podman error." not in result.stderr


def test_installer_help_mentions_verbose_flag_without_running_preflight() -> None:
    result = subprocess.run(
        ["bash", "installer/install.sh", "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "-v, --verbose" in result.stdout
    assert "--full, --ml" in result.stdout
    assert "Show full Podman probe diagnostics and Amber setup logs." in result.stdout
    assert "Checking host prerequisites" not in result.stdout


def test_installer_preflight_fails_before_release_lookup_when_podman_is_broken(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl.log"
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf '%s\\n' "$*" >> "$FAKE_CURL_LOG"
        exit 2
        """,
    )
    _write_executable(
        fake_bin / "podman",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "run" && "${2:-}" == "--help" ]]; then
          printf '%s\\n' '--userns --network --cgroups --memory --cpus --pids-limit'
          exit 0
        fi
        echo "podman is not configured" >&2
        exit 125
        """,
    )
    _write_executable(
        fake_bin / "slirp4netns",
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )

    env = os.environ.copy()
    env.update(
        {
            "AMBER_HOME": str(tmp_path / ".amber"),
            "FAKE_CURL_LOG": str(curl_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", "installer/install.sh", "indiedreamers"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Amber installer preflight failed before downloading the release." in result.stderr
    assert not curl_log.exists()


def _make_release_archive(tmp_path: Path) -> Path:
    release_root = tmp_path / "release"
    release_root.mkdir()
    amber = release_root / "amber"
    amber.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$*" >> "$AMBER_FAKE_LOG"
            if [[ "${1:-}" == "workspace" && "${2:-}" == "init" ]]; then
              workspace="${3:?workspace name required}"
              mkdir -p "$AMBER_HOME/workspaces/$workspace"
              cat > "$AMBER_HOME/workspaces/$workspace/config.toml" <<'CONFIG'
            [codex]
            podman_cgroup_manager = "none"
            enforce_resource_limits = true
            CONFIG
            fi
            if [[ "${1:-}" == "workspace" && "${2:-}" == "configure" && -n "${AMBER_FAKE_ENV_LOG:-}" ]]; then
              printf 'AMBER_CODEX_CGROUP_MANAGER=%s\\n' "${AMBER_CODEX_CGROUP_MANAGER:-}" >> "$AMBER_FAKE_ENV_LOG"
              printf 'AMBER_CODEX_ENFORCE_RESOURCE_LIMITS=%s\\n' "${AMBER_CODEX_ENFORCE_RESOURCE_LIMITS:-}" >> "$AMBER_FAKE_ENV_LOG"
            fi
            if [[ "${1:-}" == "workspace" && "${2:-}" == "configure" && -n "${AMBER_FAKE_JSON_LOG:-}" && "${AMBER_LOG_TO_STDERR:-1}" != "0" ]]; then
              printf '{"timestamp":"fixture","level":"INFO","logger":"amber.adapters.codex","message":"codex.progress","event":"codex.progress","context":{"message":"installing"}}\\n' >&2
            fi
            """
        ),
        encoding="utf-8",
    )
    amber.chmod(amber.stat().st_mode | stat.S_IXUSR)
    archive = tmp_path / "amber-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(amber, arcname="amber")
    return archive


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _add_fake_installer_prereqs(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "podman",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        if [[ "${1:-}" == "info" && "${2:-}" == "--debug" ]]; then
          cat <<'INFO'
        host:
          security:
            rootless: true
          cgroupVersion: v2
        INFO
          exit 0
        fi
        if [[ "${1:-}" == "run" && "${2:-}" == "--help" ]]; then
          printf '%s\\n' '--userns --network --cgroups --memory --cpus --pids-limit'
          exit 0
        fi
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "slirp4netns",
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
