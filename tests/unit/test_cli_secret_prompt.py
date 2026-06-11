from __future__ import annotations

from src import cli
from src.cli_input import _read_masked_chars
from src.cli_input import _read_choice_index


def test_masked_secret_reader_echoes_asterisks_without_secret() -> None:
    chars = iter("sk-test\x7fZ\n")
    output: list[str] = []

    value = _read_masked_chars(lambda: next(chars, ""), output.append)

    assert value == "sk-tesZ"
    assert "".join(output) == "*******\b \b*\n"
    assert "sk-test" not in "".join(output)


def test_masked_secret_reader_ctrl_u_clears_pasted_input() -> None:
    chars = iter("old-secret\x15new-secret\n")
    output: list[str] = []

    value = _read_masked_chars(lambda: next(chars, ""), output.append)

    assert value == "new-secret"
    assert "old-secret" not in "".join(output)


def test_choice_reader_selects_with_down_arrow_and_enter() -> None:
    chars = iter("\x1b[B\n")
    output: list[str] = []

    selected = _read_choice_index(
        lambda: next(chars, ""),
        output.append,
        "Codex CLI auth method",
        ("api-key", "device", "access-token"),
        0,
    )

    assert selected == 1
    assert "  > device" in "".join(output)


def test_choice_reader_selects_with_number_key() -> None:
    chars = iter("3\n")
    output: list[str] = []

    selected = _read_choice_index(
        lambda: next(chars, ""),
        output.append,
        "Codex CLI auth method",
        ("api-key", "device", "access-token"),
        0,
    )

    assert selected == 2


def test_codex_auth_method_uses_choice_menu_when_supported(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_read_choice(label: str, choices: tuple[str, ...], default_index: int) -> int:
        seen["label"] = label
        seen["choices"] = choices
        seen["default_index"] = default_index
        return 1

    monkeypatch.setattr(cli, "choice_menu_supported", lambda: True)
    monkeypatch.setattr(cli, "read_choice", fake_read_choice)

    method = cli._prompt_choice("Codex CLI auth method", cli.CODEX_AUTH_METHODS, "api-key")

    assert method == "device"
    assert seen == {
        "label": "Codex CLI auth method",
        "choices": cli.CODEX_AUTH_METHODS,
        "default_index": 0,
    }
