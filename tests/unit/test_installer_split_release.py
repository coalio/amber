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
