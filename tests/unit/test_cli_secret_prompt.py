from __future__ import annotations

from src.cli_input import _read_masked_chars


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
