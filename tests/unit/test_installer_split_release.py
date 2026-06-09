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

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(tmp_path / ".amber"),
            "AMBER_INSTALL_SERVICE": "n",
            "AMBER_TTY": str(tty),
            "FAKE_PART_A": str(part_a),
            "FAKE_PART_B": str(part_b),
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
    assert "Downloading split Amber v0.1.0 assets from coalio/amber..." in result.stdout
    assert (tmp_path / ".amber" / "releases" / "v0.1.0" / "amber").exists()
    assert (tmp_path / ".amber" / "bin" / "amber").resolve() == tmp_path / ".amber" / "releases" / "v0.1.0" / "amber"
    assert fake_log.read_text(encoding="utf-8").splitlines() == [
        "workspace init indiedreamers",
        "workspace configure indiedreamers",
    ]


def test_installer_reuses_cached_release_archive(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
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

    env = os.environ.copy()
    env.update(
        {
            "AMBER_FAKE_LOG": str(fake_log),
            "AMBER_HOME": str(amber_home),
            "AMBER_INSTALL_SERVICE": "n",
            "AMBER_TTY": str(tty),
            "FAKE_ARCHIVE": str(archive),
            "FAKE_CURL_LOG": str(curl_log),
            "PATH": f"{fake_bin}:{env['PATH']}",
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
    assert "Reusing downloaded Amber v0.2.0 package" in second.stdout
    assert "Extracting Amber v0.2.0..." in second.stdout
    assert (amber_home / "packages" / "v0.2.0" / "amber-linux-x86_64.tar.gz").exists()
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://api.github.com/repos/coalio/amber/releases/latest",
        "https://downloads.example/amber-linux-x86_64.tar.gz",
        "https://api.github.com/repos/coalio/amber/releases/latest",
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
            "AMBER_INSTALL_SERVICE": "n",
            "AMBER_RECOVER_TMP_PACKAGE": "1",
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
