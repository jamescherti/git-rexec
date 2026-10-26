#!/usr/bin/env python3
#
# Author: James Cherti
# URL: https://github.com/jamescherti/git-rexec
# Version: 1.0.4
#
# Copyright (C) 2019-2026 James Cherti
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.
#
"""Comprehensive pytest suite for git-rexec."""

# ruff: noqa: PLR2004

from __future__ import annotations

import argparse
import builtins
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import git_rexec

# ---------------------------------------------------------------------------
# Module-level Optional Dependency Fallbacks
# ---------------------------------------------------------------------------


def test_optional_imports_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test fallback behavior when colorama and setproctitle are missing."""
    orig_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in ("colorama", "setproctitle"):
            raise ImportError(f"No module named {name}")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    spec = importlib.util.spec_from_file_location(
        "git_rexec_no_deps", git_rexec.__file__
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)

    # Inject the module into sys.modules so @dataclass can resolve type hints
    monkeypatch.setitem(sys.modules, "git_rexec_no_deps", mod)

    spec.loader.exec_module(mod)

    assert mod.HAS_COLORAMA is False
    assert getattr(mod, "HAS_SETPROCTITLE", False) is False
    assert mod.Fore.YELLOW == ""
    assert mod.Fore.RED == ""
    assert mod.Fore.RESET == ""


# ---------------------------------------------------------------------------
# git_toplevel
# ---------------------------------------------------------------------------


def test_git_toplevel_success(tmp_path: Path) -> None:
    """Return resolved Path when git rev-parse succeeds."""
    mock_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{tmp_path}\n", stderr=""
    )
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        result = git_rexec.git_toplevel(tmp_path)
        assert result == tmp_path.resolve()
        mock_run.assert_called_once()


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.CalledProcessError(128, ["git"]),
        FileNotFoundError("git not found"),
        PermissionError("access denied"),
    ],
)
def test_git_toplevel_failure(tmp_path: Path, exc: Exception) -> None:
    """Return None when git rev-parse fails or raises OSError."""
    with patch("subprocess.run", side_effect=exc):
        assert git_rexec.git_toplevel(tmp_path) is None


# ---------------------------------------------------------------------------
# git_find_repo
# ---------------------------------------------------------------------------


def test_git_find_repo_standard_and_subrepos(tmp_path: Path) -> None:
    """Find standard repos (.git dir), sub-repos (.git file), and nested dirs."""
    # Standard repository (.git is a directory)
    repo1 = tmp_path / "repo1"
    (repo1 / ".git").mkdir(parents=True)
    # Nested directory inside repo1 should not be traversed
    (repo1 / "nested_ignored" / ".git").mkdir(parents=True)

    # Worktree / submodule (.git is a file)
    sub1 = tmp_path / "group" / "sub1"
    sub1.mkdir(parents=True)
    (sub1 / ".git").write_text("gitdir: /somewhere", encoding="utf-8")

    # Excluded directory
    excluded = tmp_path / "excluded_dir"
    (excluded / "repo_ex" / ".git").mkdir(parents=True)

    # Symlink to a directory (should not be followed)
    symlink_dir = tmp_path / "symlink_repo"
    symlink_dir.symlink_to(repo1, target_is_directory=True)

    with patch("git_rexec.git_toplevel", side_effect=lambda path: path):
        repos, sub_repos = git_rexec.git_find_repo(tmp_path, exclude_paths=[excluded])

    assert repos == {repo1.resolve()}
    assert sub_repos == {sub1.resolve()}


def test_git_find_repo_root_excluded(tmp_path: Path) -> None:
    """Return empty sets immediately if root itself is inside exclude_paths."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    repos, sub_repos = git_rexec.git_find_repo(
        tmp_path / "repo", exclude_paths=[tmp_path.resolve()]
    )
    assert repos == set()
    assert sub_repos == set()


def test_git_find_repo_oserror_scandir(tmp_path: Path) -> None:
    """Gracefully skip directories that raise OSError during os.scandir."""
    with patch("os.scandir", side_effect=PermissionError("Denied")):
        repos, sub_repos = git_rexec.git_find_repo(tmp_path)
        assert repos == set()
        assert sub_repos == set()


def test_git_find_repo_oserror_entry_is_dir(tmp_path: Path) -> None:
    """Gracefully skip directory entries whose stat/is_dir raises OSError."""
    bad_entry = MagicMock()
    bad_entry.name = "broken"
    bad_entry.is_dir.side_effect = OSError("Broken entry")

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = [bad_entry]
    mock_cm.__exit__.return_value = False

    with patch("os.scandir", return_value=mock_cm):
        repos, sub_repos = git_rexec.git_find_repo(tmp_path)
        assert repos == set()
        assert sub_repos == set()


# ---------------------------------------------------------------------------
# get_sub_repos
# ---------------------------------------------------------------------------


def test_get_sub_repos_worktrees(tmp_path: Path) -> None:
    """Parse worktrees (including bare & trailing blocks) when .git is a file."""
    worktree_repo = (tmp_path / "worktree_repo").resolve()
    wt1 = (tmp_path / "wt1").resolve()
    wt2 = (tmp_path / "wt2").resolve()
    bare_wt = (tmp_path / "bare_wt").resolve()
    missing_wt = (tmp_path / "missing_wt").resolve()

    for p in (worktree_repo, wt1, wt2, bare_wt):
        p.mkdir()

    # Create .git files for the worktrees
    for p in (worktree_repo, wt1, wt2):
        (p / ".git").write_text("gitdir: /somewhere", encoding="utf-8")

    # Includes:
    # - worktree_repo (should be ignored because it matches resolved_repo_path)
    # - bare_wt (should be ignored because of 'bare')
    # - missing_wt (should be ignored because directory does not exist)
    # - wt1 ( separated by NUL bytes )
    # - wt2 ( final block without trailing NUL byte )
    porcelain_output = (
        f"worktree {worktree_repo}\0HEAD abc\0\0"
        f"worktree {bare_wt}\0bare\0\0"
        f"worktree {missing_wt}\0HEAD def\0\0"
        f"worktree {wt1}\0HEAD 123\0\0"
        f"worktree {wt2}\0HEAD 456"
    )

    mock_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=porcelain_output, stderr=""
    )
    with patch("subprocess.run", return_value=mock_proc):
        result = git_rexec.get_sub_repos(worktree_repo)
        assert result == {wt1, wt2}


def test_get_sub_repos_submodules(tmp_path: Path) -> None:
    """Parse submodules when .gitmodules is a file."""
    main_repo = (tmp_path / "main").resolve()
    sm1 = (tmp_path / "sm1").resolve()
    sm_excl = (tmp_path / "excluded" / "sm_excl").resolve()

    for p in (main_repo, sm1, sm_excl):
        p.mkdir(parents=True)

    # Create .gitmodules so the submodule evaluation branch executes
    (main_repo / ".gitmodules").touch()

    submodule_output = f"{main_repo}\0{sm1}\0{sm_excl}\0{tmp_path / 'missing_sm'}\0"

    mock_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=submodule_output, stderr=""
    )
    with patch("subprocess.run", return_value=mock_proc):
        result = git_rexec.get_sub_repos(
            main_repo,
            resolved_exclude_paths=[(tmp_path / "excluded").resolve()],
        )
        assert result == {sm1}


def test_get_sub_repos_none(tmp_path: Path) -> None:
    """Return empty set when Git reports no other worktrees or submodules."""
    mock_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_proc):
        assert git_rexec.get_sub_repos(tmp_path.resolve()) == set()


# ---------------------------------------------------------------------------
# run_command_get_output
# ---------------------------------------------------------------------------


def test_run_command_get_output_empty(tmp_path: Path) -> None:
    """Return exit code 0 immediately when given an empty command list."""
    res = git_rexec.run_command_get_output(tmp_path, [])
    assert res.returncode == 0
    assert res.command == []


def test_run_command_get_output_success(tmp_path: Path) -> None:
    """Capture stdout, stderr, and returncode from subprocess.run."""
    mock_proc = subprocess.CompletedProcess(
        args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
    )
    with patch("subprocess.run", return_value=mock_proc):
        res = git_rexec.run_command_get_output(tmp_path, ["echo", "hi"])
        assert res.returncode == 0
        assert res.stdout == "hi\n"
        assert res.stderr == ""


def test_run_command_get_output_file_not_found(tmp_path: Path) -> None:
    """Return 127 when command binary is not found."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        res = git_rexec.run_command_get_output(tmp_path, ["missing_cmd"])
        assert res.returncode == 127
        assert "Command not found: 'missing_cmd'" in res.stderr


def test_run_command_get_output_oserror(tmp_path: Path) -> None:
    """Return 126 when an OSError (like PermissionError) occurs."""
    with patch("subprocess.run", side_effect=PermissionError("Permission denied")):
        res = git_rexec.run_command_get_output(tmp_path, ["./no_exec.sh"])
        assert res.returncode == 126
        assert "Cannot execute './no_exec.sh'" in res.stderr


# ---------------------------------------------------------------------------
# format_parallel_output
# ---------------------------------------------------------------------------


def test_format_parallel_output_empty_success(tmp_path: Path) -> None:
    """Return empty string when command succeeds with no output."""
    res = git_rexec.CommandResult(command=["git", "status"], returncode=0)
    assert git_rexec.format_parallel_output(tmp_path, res) == ""


def test_format_parallel_output_empty_failure(tmp_path: Path) -> None:
    """Return header even when output is empty if returncode is non-zero."""
    res = git_rexec.CommandResult(command=["git", "status"], returncode=1)
    out = git_rexec.format_parallel_output(tmp_path, res, quiet=False)
    assert f"[EXEC-P] {tmp_path}: git status" in out


def test_format_parallel_output_normal_and_quiet(tmp_path: Path) -> None:
    """Format indentation normally and preserve original output in quiet mode."""
    res = git_rexec.CommandResult(
        command=["printf", "a\tb"],
        returncode=0,
        stdout="col1\tcol2\n",
        stderr="err\n",
    )
    normal = git_rexec.format_parallel_output(tmp_path, res, quiet=False)
    assert f"[EXEC-P] {tmp_path}: printf" in normal
    assert "    col1    col2\n    err\n" in normal

    quiet = git_rexec.format_parallel_output(tmp_path, res, quiet=True)
    assert "[EXEC-P]" not in quiet
    assert quiet == "col1\tcol2\nerr\n"


def test_executable_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept repository-local commands and check PATH for other executables."""
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/git" if cmd == "git" else None
    )
    assert shutil.which("git")
    assert not shutil.which("missing-binary")
    assert not shutil.which("/missing/absolute-script")


# ---------------------------------------------------------------------------
# process_repo
# ---------------------------------------------------------------------------


def test_process_repo_filtered_out(tmp_path: Path) -> None:
    """Return None when the --if-exec filter command exits non-zero."""
    with patch(
        "git_rexec.run_command_get_output",
        return_value=git_rexec.CommandResult(["false"], returncode=1),
    ):
        ctx = git_rexec.process_repo(tmp_path, ["git", "status"], ["false"])
        assert ctx is None


def test_process_repo_filter_passes_and_executes(tmp_path: Path) -> None:
    """Run parallel command when --if-exec succeeds."""
    side_effects = [
        git_rexec.CommandResult(["true"], returncode=0),
        git_rexec.CommandResult(["git", "status"], returncode=0, stdout="ok\n"),
    ]
    with patch("git_rexec.run_command_get_output", side_effect=side_effects):
        ctx = git_rexec.process_repo(tmp_path, ["git", "status"], ["true"])
        assert ctx is not None
        assert ctx.path == tmp_path
        assert ctx.parallel_result is not None
        assert ctx.parallel_result.stdout == "ok\n"


def test_process_repo_no_commands(tmp_path: Path) -> None:
    """Return RepoContext with parallel_result=None when no commands are passed."""
    ctx = git_rexec.process_repo(tmp_path, None, None)
    assert ctx is not None
    assert ctx.parallel_result is None


# ---------------------------------------------------------------------------
# discover_repos
# ---------------------------------------------------------------------------


def test_discover_repos_from_toplevel_with_subrepos_and_excludes(
    tmp_path: Path,
) -> None:
    """Discover top-level repo, fetch sub-repos, and filter excluded paths."""
    top = tmp_path / "top"
    sub_keep = tmp_path / "top" / "sub_keep"
    sub_excl = tmp_path / "top" / "excluded" / "sub_excl"
    for p in (top, sub_keep, sub_excl):
        p.mkdir(parents=True)

    with (
        patch("git_rexec.git_toplevel", return_value=top),
        patch("git_rexec.get_sub_repos", return_value={sub_keep, sub_excl}),
    ):
        repos = git_rexec.discover_repos(
            directory=top,
            max_workers=2,
            exclude_dirs=[str(tmp_path / "top" / "excluded")],
            include_sub_repos=True,
        )
        assert repos == {top, sub_keep}


def test_discover_repos_via_find_repo_no_excludes(tmp_path: Path) -> None:
    """Fallback to git_find_repo when directory is not inside a single git repo."""
    r1 = tmp_path / "r1"
    wt1 = tmp_path / "wt1"
    r1.mkdir()
    wt1.mkdir()

    with (
        patch("git_rexec.git_toplevel", return_value=None),
        patch("git_rexec.git_find_repo", return_value=({r1}, {wt1})),
        patch("git_rexec.get_sub_repos", return_value={wt1}),
    ):
        repos = git_rexec.discover_repos(
            directory=tmp_path,
            max_workers=1,
            exclude_dirs=[],
            include_sub_repos=True,
        )
        assert repos == {r1, wt1}


# ---------------------------------------------------------------------------
# execute_parallel_tasks
# ---------------------------------------------------------------------------


def test_execute_parallel_tasks_success(tmp_path: Path) -> None:
    """Collect and sort successful repository contexts."""
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"

    def fake_process(repo: Path, *_args: object) -> git_rexec.RepoContext:
        return git_rexec.RepoContext(path=repo)

    with patch("git_rexec.process_repo", side_effect=fake_process):
        results = git_rexec.execute_parallel_tasks({r2, r1}, None, None, max_workers=2)

    assert [result.path for result in results] == [r1, r2]


@pytest.mark.parametrize("error", [OSError("disk error"), ValueError("invalid data")])
def test_execute_parallel_tasks_exception(tmp_path: Path, error: Exception) -> None:
    """Propagate worker errors with repository context and preserve the cause."""
    repo = tmp_path / "repo"

    with (
        patch("git_rexec.process_repo", side_effect=error),
        pytest.raises(RuntimeError) as exc_info,
    ):
        git_rexec.execute_parallel_tasks({repo}, None, None, max_workers=2)

    assert str(exc_info.value) == f"Error processing repository {repo}: {error}"
    assert exc_info.value.__cause__ is error


# ---------------------------------------------------------------------------
# parse_args & print_error_summary
# ---------------------------------------------------------------------------


def test_parse_args_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse CLI flags accurately."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "git-rexec",
            "-C",
            "/tmp",
            "--exclude-dir",
            "vendor",
            "-p",
            "-i",
            "git status",
            "-j",
            "4",
            "-q",
            "-s",
            "--print",
            "--",
            "git",
            "fetch",
        ],
    )
    args = git_rexec.parse_args()
    assert args.directory == Path("/tmp")
    assert args.exclude_dir == ["vendor"]
    assert args.parallel is True
    assert args.if_exec == "git status"
    assert args.max_workers == 4
    assert args.quiet is True
    assert args.include_sub_repos is True
    assert args.print is True
    assert args.exec_cmd == ["git", "fetch"]


def test_parse_args_jobs_zero_becomes_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure passing -j 0 sets max_workers to 1 due to lambda max(1, int(x))."""
    monkeypatch.setattr(sys, "argv", ["git-rexec", "-j", "0", "--print"])
    args = git_rexec.parse_args()
    assert args.max_workers == 1


def test_parse_args_invalid_jobs_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit with error when --jobs is not an integer string."""
    monkeypatch.setattr(sys, "argv", ["git-rexec", "-j", "foo", "--print"])
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.parse_args()
    assert exc_info.value.code == 2


def test_print_error_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Return 0 for no errors, or print details and return 1 when errors exist."""
    assert git_rexec.print_error_summary([]) == 0

    errors = [
        (tmp_path / "r1", git_rexec.CommandResult(["missing"], returncode=127)),
        (tmp_path / "r2", git_rexec.CommandResult(["git", "pull"], returncode=2)),
        (tmp_path / "r3", git_rexec.CommandResult(["ok"], returncode=0)),
    ]
    rc = git_rexec.print_error_summary(errors)
    assert rc == 1
    out = capsys.readouterr().out
    assert "Command not found: missing" in out
    assert "errno 2: git pull" in out


# ---------------------------------------------------------------------------
# main() Execution Flows
# ---------------------------------------------------------------------------


def _make_args(**overrides: object) -> argparse.Namespace:
    defaults = {
        "directory": Path(),
        "exclude_dir": [],
        "parallel": False,
        "if_exec": None,
        "max_workers": 2,
        "quiet": False,
        "include_sub_repos": False,
        "print": False,
        "print0": False,
        "exec_cmd": [],
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_main_nothing_to_do(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit with code 2 when no action flags or commands are given."""
    monkeypatch.setattr(git_rexec, "parse_args", _make_args)
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 2
    assert "Nothing to do." in capsys.readouterr().err


def test_main_missing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with code 1 when -C directory does not exist."""
    bad_dir = tmp_path / "does_not_exist"
    monkeypatch.setattr(
        git_rexec, "parse_args", lambda: _make_args(directory=bad_dir, print=True)
    )
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 1
    assert "Directory not found" in capsys.readouterr().err


def test_main_command_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with 127 when exec_cmd binary is not in PATH."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directory=tmp_path, exec_cmd=["nonexistent_bin"]),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 127
    assert "Command not found" in capsys.readouterr().err


def test_main_invalid_if_exec_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with code 2 when --if-exec has unbalanced quotes."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directory=tmp_path, print=True, if_exec="echo 'unclosed"),
    )
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 2
    assert "Error parsing --if-exec" in capsys.readouterr().err


def test_main_empty_if_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with code 2 when --if-exec is empty or whitespace only."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directory=tmp_path, print=True, if_exec="   "),
    )
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 2
    assert "--if-exec command cannot be empty" in capsys.readouterr().err


def test_main_if_exec_command_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with 127 when --if-exec binary is not in PATH."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directory=tmp_path, print=True, if_exec="missing_filter"),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 127
    assert "Command not found: 'missing_filter'" in capsys.readouterr().err


def test_main_worker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit unsuccessfully when a parallel worker raises RuntimeError."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directory=tmp_path, parallel=True, exec_cmd=["git"]),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/git")
    monkeypatch.setattr(git_rexec, "discover_repos", lambda *_a: {tmp_path})
    monkeypatch.setattr(
        git_rexec,
        "execute_parallel_tasks",
        MagicMock(side_effect=RuntimeError("worker failed")),
    )

    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()

    assert exc_info.value.code == 1
    assert "worker failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("print_flag", "print0_flag", "expected_end"),
    [(True, False, "\n"), (False, True, "\0")],
)
def test_main_print_modes(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    print_flag: bool,
    print0_flag: bool,
    expected_end: str,
) -> None:
    """Test --print and --print0 output formatting."""
    monkeypatch.setattr(git_rexec, "HAS_SETPROCTITLE", True)
    monkeypatch.setattr(git_rexec, "setproctitle", MagicMock(), raising=False)
    monkeypatch.setattr(git_rexec, "HAS_COLORAMA", True)
    monkeypatch.setattr(git_rexec, "colorama_init", MagicMock(), raising=False)
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/true")

    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(
            directory=tmp_path,
            print=print_flag,
            print0=print0_flag,
            if_exec="true",
        ),
    )
    monkeypatch.setattr(git_rexec, "discover_repos", lambda *_a: {tmp_path})
    monkeypatch.setattr(
        git_rexec,
        "execute_parallel_tasks",
        lambda *_a: [git_rexec.RepoContext(path=tmp_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"{tmp_path}{expected_end}"


def test_main_parallel_execution_with_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Display parallel output and report non-zero exit code in summary."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(
            directory=tmp_path, parallel=True, exec_cmd=["git", "status"]
        ),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/git")
    monkeypatch.setattr(git_rexec, "discover_repos", lambda *_a: {tmp_path})

    p_res = git_rexec.CommandResult(
        command=["git", "status"], returncode=1, stdout="dirty\n"
    )
    monkeypatch.setattr(
        git_rexec,
        "execute_parallel_tasks",
        lambda *_a: [git_rexec.RepoContext(path=tmp_path, parallel_result=p_res)],
    )

    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "dirty" in out
    assert "Errors:" in out


@pytest.mark.parametrize(
    ("side_effect", "expected_rc"),
    [
        (None, 0),
        (subprocess.CalledProcessError(3, ["git"]), 1),
        (FileNotFoundError(), 1),
        (PermissionError("denied"), 1),
    ],
)
def test_main_foreground_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    side_effect: Exception | None,
    expected_rc: int,
) -> None:
    """Test sequential foreground execution success and exception branches."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(
            directory=tmp_path, parallel=False, quiet=False, exec_cmd=["git", "status"]
        ),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/git")
    monkeypatch.setattr(git_rexec, "discover_repos", lambda *_a: {tmp_path})
    monkeypatch.setattr(
        git_rexec,
        "execute_parallel_tasks",
        lambda *_a: [git_rexec.RepoContext(path=tmp_path)],
    )

    with patch("subprocess.check_call", side_effect=side_effect):
        with pytest.raises(SystemExit) as exc_info:
            git_rexec.main()
        assert exc_info.value.code == expected_rc

    out = capsys.readouterr().out
    assert f"[EXEC] {tmp_path}: git status" in out


# ---------------------------------------------------------------------------
# __main__ Entrypoint Signal Handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (KeyboardInterrupt(), 130),
        (BrokenPipeError(), 141),
    ],
)
def test_dunder_main_signals(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException, expected_code: int
) -> None:
    """Verify __main__ catches KeyboardInterrupt and BrokenPipeError."""
    monkeypatch.setattr(git_rexec, "_run", MagicMock(side_effect=exc))
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == expected_code
