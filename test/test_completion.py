"""Tests for `triage completion` and `triage list --names`."""

import os
import pathlib
import shutil
import subprocess
from collections.abc import Callable

import pytest

from triage_cli import cli
from triage_cli import completion

TRACKED = ["BaranziniLab/biorouter", "vorssaint/vorssaint-utils"]


@pytest.mark.usefixtures("tracked")
def test_list_names_prints_only_names(
    triage: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("list", "--names") == 0
    assert capsys.readouterr().out == "vorssaint/vorssaint-utils\n"


@pytest.mark.usefixtures("tracked")
def test_list_names_skips_unreadable_files(
    triage: Callable[..., int],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "broken__file.db").write_bytes(b"not a database")

    assert triage("list", "--names") == 0
    assert capsys.readouterr().out == "vorssaint/vorssaint-utils\n"


def test_list_names_with_nothing_tracked_prints_nothing(
    triage: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("list", "--names") == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("shell", "registration"),
    [
        ("bash", "complete -o default -F _triage triage"),
        ("zsh", "compdef _triage triage"),
    ],
)
def test_completion_prints_the_script(
    shell: str, registration: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["completion", shell]) == 0
    assert registration in capsys.readouterr().out


@pytest.fixture
def stub_triage(tmp_path: pathlib.Path) -> dict[str, str]:
    """Returns an environment whose `triage` prints two tracked repos."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "triage"
    stub.write_text(
        "#!/bin/sh\nprintf '%s\\n' " + " ".join(TRACKED) + "\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _bash_complete(words: list[str], env: dict[str, str]) -> list[str]:
    """Runs the bash completion function for a command line."""
    script = completion.SCRIPTS["bash"]
    quoted = " ".join(f"'{word}'" for word in words)
    program = (
        f"{script}\n"
        f"COMP_WORDS=({quoted}); COMP_CWORD={len(words) - 1}\n"
        '_triage; printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", program],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


bash_only = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not installed"
)


@bash_only
@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (
            ["triage", ""],
            ["setup", "add", "sync", "list", "remove", "completion"],
        ),
        (["triage", "re"], ["remove"]),
        (["triage", "remove", ""], TRACKED),
        (["triage", "sync", ""], TRACKED),
        (["triage", "sync", "vor"], ["vorssaint/vorssaint-utils"]),
        (["triage", "remove", "vor"], ["vorssaint/vorssaint-utils"]),
        (["triage", "-v", "remove", "Bar"], ["BaranziniLab/biorouter"]),
        (
            ["triage", "--data-dir", "x", "remove", "vor"],
            ["vorssaint/vorssaint-utils"],
        ),
        (["triage", "completion", "z"], ["zsh"]),
    ],
)
def test_bash_completion(
    words: list[str], expected: list[str], stub_triage: dict[str, str]
) -> None:
    assert _bash_complete(words, stub_triage) == expected


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_zsh_script_parses(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "triage.zsh"
    script.write_text(completion.SCRIPTS["zsh"], encoding="utf-8")
    subprocess.run(["zsh", "-n", str(script)], check=True)
