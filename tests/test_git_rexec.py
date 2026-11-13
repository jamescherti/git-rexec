#!/usr/bin/env python3
#
# Author: James Cherti
# URL: https://github.com/jamescherti/git-rexec
# Version: 1.1.2
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

from __future__ import annotations

import argparse
import builtins
import importlib.util
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
    """Test fallback behavior when colorama, setproctitle, and pygit2 are missing."""
    orig_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in ("colorama", "setproctitle", "pygit2"):
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
    # TODO
    # assert getattr(mod, "HAS_PYGIT2", False) is False
    assert mod.Fore.YELLOW == ""
    assert mod.Fore.RED == ""
    assert mod.Fore.RESET == ""


# ---------------------------------------------------------------------------
# GitBackend (init & git CLI backend)
# ---------------------------------------------------------------------------


def test_git_backend_invalid() -> None:
    """Raise ValueError when an unsupported backend is requested."""
    with pytest.raises(ValueError, match="Unsupported Git backend: libgit"):
        git_rexec.GitBackend(backend="libgit")


def test_git_toplevel_success(tmp_path: Path) -> None:
    """Return resolved Path when git rev-parse succeeds."""
    backend = git_rexec.GitBackend(backend="git")
    mock_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{tmp_path}\n", stderr=""
    )
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        result = backend.toplevel(tmp_path)
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
    backend = git_rexec.GitBackend(backend="git")
    with patch("subprocess.run", side_effect=exc):
        assert backend.toplevel(tmp_path) is None


# ---------------------------------------------------------------------------
# GitBackend (pygit2 backend)
# ---------------------------------------------------------------------------


class DummyGitError(Exception):
    """Dummy Git error."""

    pass


@pytest.fixture
def mock_pygit2(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock pygit2."""
    fake_pygit2 = MagicMock()
    fake_pygit2.GitError = DummyGitError
    monkeypatch.setattr(git_rexec.git_backend, "HAS_PYGIT2", True)
    monkeypatch.setattr(git_rexec.git_backend, "pygit2", fake_pygit2, raising=False)
    return fake_pygit2


def test_git_backend_pygit2_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise ValueError if pygit2 is requested but not available."""
    monkeypatch.setattr(git_rexec.git_backend, "HAS_PYGIT2", False)
    with pytest.raises(ValueError):
        git_rexec.GitBackend(backend="pygit2")


def test_git_toplevel_pygit2_success(tmp_path: Path, mock_pygit2: MagicMock) -> None:
    """Return resolved Path when pygit2 successfully discovers a repository."""
    mock_pygit2.discover_repository.return_value = str(tmp_path / ".git")
    mock_repo = MagicMock()
    mock_repo.is_bare = False
    mock_repo.workdir = str(tmp_path)
    mock_pygit2.Repository.return_value = mock_repo

    backend = git_rexec.GitBackend(backend="pygit2")
    result = backend.toplevel(tmp_path)
    assert result == tmp_path.resolve()


def test_git_toplevel_pygit2_not_found(tmp_path: Path, mock_pygit2: MagicMock) -> None:
    """Return None when pygit2 fails to discover a repository."""
    mock_pygit2.discover_repository.return_value = None

    backend = git_rexec.GitBackend(backend="pygit2")
    result = backend.toplevel(tmp_path)
    assert result is None


def test_git_toplevel_pygit2_exception(tmp_path: Path, mock_pygit2: MagicMock) -> None:
    """Return None when pygit2 raises a GitError."""
    mock_pygit2.discover_repository.side_effect = DummyGitError("Not a repo")

    backend = git_rexec.GitBackend(backend="pygit2")
    result = backend.toplevel(tmp_path)
    assert result is None


def test_list_worktrees_pygit2(tmp_path: Path, mock_pygit2: MagicMock) -> None:
    """Return worktree paths using pygit2."""
    mock_repo = MagicMock()
    mock_repo.is_bare = False
    mock_repo.workdir = str(tmp_path)
    mock_repo.listall_worktrees.return_value = ["wt1"]

    mock_wt = MagicMock()
    mock_wt.path = str(tmp_path / "wt1")
    mock_repo.lookup_worktree.return_value = mock_wt
    mock_pygit2.Repository.return_value = mock_repo

    backend = git_rexec.GitBackend(backend="pygit2")
    result = backend.list_worktrees(tmp_path)
    assert result == {tmp_path.resolve(), (tmp_path / "wt1").resolve()}


def test_list_submodules_pygit2(tmp_path: Path, mock_pygit2: MagicMock) -> None:
    """Return submodule paths using pygit2 listall_submodules."""
    mock_repo = MagicMock()
    mock_repo.is_bare = False
    mock_repo.workdir = str(tmp_path)
    mock_repo.listall_submodules.return_value = ["sm1"]

    mock_sm = MagicMock()
    mock_sm.path = "sm1"
    mock_repo.lookup_submodule.return_value = mock_sm
    mock_pygit2.Repository.return_value = mock_repo

    backend = git_rexec.GitBackend(backend="pygit2")
    result = backend.list_submodules(tmp_path)
    assert result == {(tmp_path / "sm1").resolve()}


def test_list_submodules_pygit2_fallback(
    tmp_path: Path, mock_pygit2: MagicMock
) -> None:
    """Return submodule paths using pygit2 submodules property."""
    mock_repo = MagicMock()
    mock_repo.is_bare = False
    mock_repo.workdir = str(tmp_path)
    del mock_repo.listall_submodules  # Force using the .submodules property

    mock_sm = MagicMock()
    mock_sm.path = "sm1"
    mock_repo.submodules = [mock_sm]
    mock_pygit2.Repository.return_value = mock_repo

    backend = git_rexec.GitBackend(backend="pygit2")
    result = backend.list_submodules(tmp_path)
    assert result == {(tmp_path / "sm1").resolve()}


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

    repos, sub_repos = git_rexec.git_find_repo(
        {tmp_path}, resolved_exclude_paths=[excluded]
    )

    assert repos == {repo1.resolve()}
    assert sub_repos == {sub1.resolve()}


def test_git_find_repo_root_excluded(tmp_path: Path) -> None:
    """Return empty sets immediately if root itself is inside exclude_paths."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    repos, sub_repos = git_rexec.git_find_repo(
        {tmp_path / "repo"}, resolved_exclude_paths=[tmp_path.resolve()]
    )
    assert repos == set()
    assert sub_repos == set()


def test_git_find_repo_oserror_scandir(tmp_path: Path) -> None:
    """Gracefully skip directories that raise OSError during os.scandir."""
    with patch("os.scandir", side_effect=PermissionError("Denied")):
        repos, sub_repos = git_rexec.git_find_repo({tmp_path})
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
        repos, sub_repos = git_rexec.git_find_repo({tmp_path})
        assert repos == set()
        assert sub_repos == set()


# ---------------------------------------------------------------------------
# get_sub_repos (and GitBackend.list_worktrees / list_submodules)
# ---------------------------------------------------------------------------


def test_get_sub_repos_worktrees(tmp_path: Path) -> None:
    """Parse worktrees (including bare & trailing blocks) when .git is a file."""
    backend = git_rexec.GitBackend(backend="git")
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
        result = git_rexec.get_sub_repos(backend, worktree_repo)
        assert result == {wt1, wt2}


def test_get_sub_repos_submodules(tmp_path: Path) -> None:
    """Parse submodules when .gitmodules is a file."""
    backend = git_rexec.GitBackend(backend="git")
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
            backend,
            main_repo,
            resolved_exclude_paths=[(tmp_path / "excluded").resolve()],
        )
        assert result == {sm1}


def test_get_sub_repos_none(tmp_path: Path) -> None:
    """Return empty set when Git reports no other worktrees or submodules."""
    backend = git_rexec.GitBackend(backend="git")
    mock_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_proc):
        assert git_rexec.get_sub_repos(backend, tmp_path.resolve()) == set()


# ---------------------------------------------------------------------------
# command_exists & run_command_get_output
# ---------------------------------------------------------------------------


def test_command_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check command availability across cwd=None, relative paths, and PATH."""
    assert git_rexec.command_exists("") is False
    assert git_rexec.command_exists("git", cwd=tmp_path / "nonexistent") is False

    script = tmp_path / "local_script.sh"
    script.touch(mode=0o755)

    monkeypatch.setattr(
        "shutil.which",
        lambda cmd, **_kw: cmd if cmd in ("git", str(script)) else None,
    )

    # cwd is None
    monkeypatch.chdir(tmp_path)
    assert git_rexec.command_exists("git", cwd=None) is True
    assert git_rexec.command_exists("missing-binary", cwd=None) is False
    assert git_rexec.command_exists("./local_script.sh", cwd=None) is True
    assert git_rexec.command_exists("./missing_script.sh", cwd=None) is False

    # cwd is provided with relative path containing separator
    assert git_rexec.command_exists("./missing_script.sh", cwd=tmp_path) is False
    assert git_rexec.command_exists("./local_script.sh", cwd=tmp_path) is True

    # Absolute path
    assert git_rexec.command_exists(str(script), cwd=tmp_path) is True
    assert git_rexec.command_exists(str(tmp_path / "missing.sh"), cwd=tmp_path) is False

    # cwd is provided with standard binary name
    assert git_rexec.command_exists("git", cwd=tmp_path) is True
    assert git_rexec.command_exists("missing-binary", cwd=tmp_path) is False


def test_run_command_get_output_empty(tmp_path: Path) -> None:
    """Return exit code 0 immediately when given an empty command list."""
    res = git_rexec.command_execution.run_command_get_output(tmp_path, [])
    assert res.returncode == 0
    assert res.command == []


def test_run_command_get_output_success(tmp_path: Path) -> None:
    """Capture stdout, stderr, and returncode from subprocess.run."""
    mock_proc = subprocess.CompletedProcess(
        args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
    )
    with (
        patch("git_rexec.command_exists", return_value=True),
        patch("subprocess.run", return_value=mock_proc),
    ):
        res = git_rexec.command_execution.run_command_get_output(
            tmp_path, ["echo", "hi"]
        )
        assert res.returncode == 0
        assert res.stdout == "hi\n"
        assert res.stderr == ""


def test_run_command_get_output_file_not_found(tmp_path: Path) -> None:
    """Return 127 when command binary is not found."""
    with patch("git_rexec.command_exists", return_value=False):
        res = git_rexec.command_execution.run_command_get_output(
            tmp_path, ["missing_cmd"]
        )
        assert res.returncode == 127
        assert "Command not found: 'missing_cmd'" in res.stderr


def test_run_command_get_output_oserror(tmp_path: Path) -> None:
    """Return 126 when an OSError (like PermissionError) occurs."""
    with (
        patch("git_rexec.command_execution.command_exists", return_value=True),
        patch("subprocess.run", side_effect=PermissionError("Permission denied")),
    ):
        res = git_rexec.command_execution.run_command_get_output(
            tmp_path, ["./no_exec.sh"]
        )
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


# ---------------------------------------------------------------------------
# process_repo
# ---------------------------------------------------------------------------


def test_process_repo_filtered_out(tmp_path: Path) -> None:
    """Return None when the --if-exec filter command exits non-zero."""
    with patch(
        "git_rexec.command_execution.run_command_get_output",
        return_value=git_rexec.CommandResult(["false"], returncode=1),
    ):
        ctx = git_rexec.command_execution.process_repo(
            tmp_path, ["git", "status"], ["false"]
        )
        assert ctx is None


def test_process_repo_filter_passes_and_executes(tmp_path: Path) -> None:
    """Run parallel command when --if-exec succeeds."""
    side_effects = [
        git_rexec.CommandResult(["true"], returncode=0),
        git_rexec.CommandResult(["git", "status"], returncode=0, stdout="ok\n"),
    ]
    with patch(
        "git_rexec.command_execution.run_command_get_output", side_effect=side_effects
    ):
        ctx = git_rexec.command_execution.process_repo(
            tmp_path, ["git", "status"], ["true"]
        )
        assert ctx is not None
        assert ctx.path == tmp_path
        assert ctx.parallel_result is not None
        assert ctx.parallel_result.stdout == "ok\n"


def test_process_repo_no_commands(tmp_path: Path) -> None:
    """Return RepoContext with parallel_result=None when no commands are passed."""
    ctx = git_rexec.command_execution.process_repo(tmp_path, None, None)
    assert ctx is not None
    assert ctx.parallel_result is None


# ---------------------------------------------------------------------------
# discover_repos
# ---------------------------------------------------------------------------


def test_discover_repos_from_toplevel_with_subrepos_and_excludes(
    tmp_path: Path,
) -> None:
    """Discover top-level repo, fetch sub-repos, and filter excluded paths."""
    backend = git_rexec.GitBackend(backend="git")
    top = tmp_path / "top"
    sub_keep = tmp_path / "top" / "sub_keep"
    sub_excl = tmp_path / "top" / "excluded" / "sub_excl"
    for p in (top, sub_keep, sub_excl):
        p.mkdir(parents=True)

    (top / ".git").mkdir()
    (top / ".gitmodules").touch()

    with (
        patch.object(backend, "toplevel", return_value=top),
        patch.object(backend, "list_submodules", return_value={sub_keep, sub_excl}),
    ):
        repos = git_rexec.discover_repos(
            git_backend=backend,
            directories=[top],
            max_workers=2,
            exclude_dirs=[str(tmp_path / "top" / "excluded")],
            include_sub_repos=True,
        )
        assert repos == {top, sub_keep}


def test_discover_repos_via_find_repo_no_excludes(tmp_path: Path) -> None:
    """Fallback to git_find_repo when directory is not inside a single git repo."""
    backend = git_rexec.GitBackend(backend="git")
    r1 = tmp_path / "r1"
    wt1 = tmp_path / "wt1"
    r1.mkdir()
    wt1.mkdir()

    with (
        patch.object(backend, "toplevel", return_value=None),
        patch("git_rexec.git_find_repo", return_value=({r1}, {wt1})),
        patch("git_rexec.get_sub_repos", return_value={wt1}),
    ):
        repos = git_rexec.discover_repos(
            git_backend=backend,
            directories=[tmp_path],
            max_workers=1,
            exclude_dirs=[],
            include_sub_repos=True,
        )
        assert repos == {r1}


def test_discover_repos_root_excluded(tmp_path: Path) -> None:
    """Return empty set immediately if root directory itself is excluded."""
    backend = git_rexec.GitBackend(backend="git")
    repos = git_rexec.discover_repos(
        git_backend=backend,
        directories=[tmp_path],
        max_workers=1,
        exclude_dirs=[str(tmp_path)],
        include_sub_repos=False,
    )
    assert repos == set()


# ---------------------------------------------------------------------------
# execute_parallel_tasks
# ---------------------------------------------------------------------------


def test_execute_parallel_tasks_empty() -> None:
    """Return empty list immediately when repos set is empty."""
    assert git_rexec.execute_parallel_tasks(set(), None, None, max_workers=1) == []


def test_execute_parallel_tasks_success(tmp_path: Path) -> None:
    """Collect and sort successful repository contexts."""
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"

    def fake_process(repo: Path, *_args: object) -> git_rexec.RepoContext:
        return git_rexec.RepoContext(path=repo)

    with patch("git_rexec.command_execution.process_repo", side_effect=fake_process):
        results = git_rexec.execute_parallel_tasks({r2, r1}, None, None, max_workers=2)

    assert [result.path for result in results] == [r1, r2]


@pytest.mark.parametrize("error", [OSError("disk error"), ValueError("invalid data")])
def test_execute_parallel_tasks_exception(tmp_path: Path, error: Exception) -> None:
    """Propagate worker errors with repository context and preserve the cause."""
    repo = tmp_path / "repo"

    with (
        patch("git_rexec.command_execution.process_repo", side_effect=error),
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
            "--directory",
            "projects",
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
    assert args.directories == [Path("/tmp"), Path("projects")]
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
    defaults: dict[str, Any] = {
        "directories": None,
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


@pytest.mark.parametrize("explicit_roots", [True, False], ids=["repeated-C", "cwd"])
def test_main_shared_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explicit_roots: bool,
) -> None:
    """Verify recursive discovery and a single scan of overlapping directories."""
    first = tmp_path / "first"
    nested = first / "nested"
    second = tmp_path / "second"
    repos = [nested / "one", second / "nested" / "two"]
    unrelated = tmp_path / "other" / "three"
    for repo in [*repos, unrelated]:
        (repo / ".git").mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    options = ["-C", "first", "-C", "second", "-C", "first/nested", "-C", "first"]
    monkeypatch.setattr(
        sys, "argv", ["git-rexec", *(options if explicit_roots else []), "--print"]
    )
    with (
        patch.object(git_rexec.GitBackend, "toplevel", return_value=None),
        patch.object(
            git_rexec, "discover_repos", wraps=git_rexec.discover_repos
        ) as discover,
        patch.object(git_rexec.os, "scandir", wraps=git_rexec.os.scandir) as scan,
        pytest.raises(SystemExit) as status,
    ):
        git_rexec.main()

    assert status.value.code == 0
    discover.assert_called_once()
    expected = repos if explicit_roots else [*repos, unrelated]
    assert capsys.readouterr().out.splitlines() == sorted(map(str, expected))
    scanned = [call.args[0] for call in scan.call_args_list]
    assert nested in scanned
    assert len(scanned) == len(set(scanned))


def test_main_missing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with code 1 when -C directory does not exist."""
    bad_dir = tmp_path / "does_not_exist"
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directories=[tmp_path, bad_dir], print=True),
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
    """Exit with 1 and print error summary when exec_cmd binary is not in PATH."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directories=[tmp_path], exec_cmd=["nonexistent_bin"]),
    )
    monkeypatch.setattr(git_rexec, "discover_repos", lambda *_a: {tmp_path})
    monkeypatch.setattr("shutil.which", lambda _cmd, **_kw: None)
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 1
    assert "Command not found: nonexistent_bin" in capsys.readouterr().out


def test_main_invalid_if_exec_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with code 2 when --if-exec has unbalanced quotes."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(
            directories=[tmp_path], print=True, if_exec="echo 'unclosed"
        ),
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
        lambda: _make_args(directories=[tmp_path], print=True, if_exec="   "),
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
    """Filter out repo and exit 0 when --if-exec binary is not in PATH."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(
            directories=[tmp_path], print=True, if_exec="missing_filter"
        ),
    )
    monkeypatch.setattr(git_rexec, "discover_repos", lambda *_a: {tmp_path})
    monkeypatch.setattr("shutil.which", lambda _cmd, **_kw: None)
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == ""


def test_main_discover_repos_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit with code 1 when discover_repos raises an OSError."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directories=[tmp_path], print=True),
    )
    monkeypatch.setattr(
        git_rexec,
        "discover_repos",
        MagicMock(side_effect=OSError("discovery failed")),
    )
    with pytest.raises(SystemExit) as exc_info:
        git_rexec.main()
    assert exc_info.value.code == 1
    assert "Error discovering repositories: discovery failed" in capsys.readouterr().err


def test_main_worker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit unsuccessfully when a parallel worker raises RuntimeError."""
    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(directories=[tmp_path], parallel=True, exec_cmd=["git"]),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd, **_kw: "/usr/bin/git")
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
    monkeypatch.setattr("shutil.which", lambda _cmd, **_kw: "/usr/bin/true")

    monkeypatch.setattr(
        git_rexec,
        "parse_args",
        lambda: _make_args(
            directories=[tmp_path],
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
            directories=[tmp_path], parallel=True, exec_cmd=["git", "status"]
        ),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd, **_kw: "/usr/bin/git")
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
            directories=[tmp_path],
            parallel=False,
            quiet=False,
            exec_cmd=["git", "status"],
        ),
    )
    monkeypatch.setattr("shutil.which", lambda _cmd, **_kw: "/usr/bin/git")
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


@pytest.mark.parametrize(
    "kind",
    ["missing", "file", "directory", "symlink", "broken_symlink", "symlink_directory"],
)
def test_has_ignore_marker(tmp_path: Path, kind: str) -> None:
    """Accept regular files and symlinks to files as ignore markers."""
    marker = tmp_path / ".git-rexec-ignore"
    if kind == "file":
        marker.touch()
    elif kind == "directory":
        marker.mkdir()
    elif kind in {"symlink", "broken_symlink", "symlink_directory"}:
        target = tmp_path / "target"
        if kind == "symlink":
            target.touch()
        elif kind == "symlink_directory":
            target.mkdir()
        try:
            marker.symlink_to(target, target_is_directory=kind == "symlink_directory")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Symbolic links are unavailable: {exc}")

    assert git_rexec.has_ignore_marker(tmp_path) is (kind in {"file", "symlink"})


@pytest.mark.parametrize("git_file", [False, True], ids=["repository", "worktree"])
@pytest.mark.parametrize(
    "marker_first", [False, True], ids=["git-first", "marker-first"]
)
def test_git_find_repo_marker_precedes_repository_detection(
    tmp_path: Path, git_file: bool, marker_first: bool
) -> None:
    """Exclude marked repositories regardless of directory entry order."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if git_file:
        (repo / ".git").touch()
    else:
        (repo / ".git").mkdir()
    (repo / ".git-rexec-ignore").touch()
    original_scandir = git_rexec.os.scandir

    def ordered_scandir(path: Path) -> Any:
        """Supply a controlled entry order for the marked repository.

        :param path: Directory to scan.
        :return: A directory scanning context manager.
        """
        if Path(path) != repo:
            return original_scandir(path)
        with original_scandir(path) as entries:
            ordered = sorted(
                entries,
                key=lambda entry: entry.name == ".git-rexec-ignore",
                reverse=marker_first,
            )
        context = MagicMock()
        context.__enter__.return_value = iter(ordered)
        context.__exit__.return_value = False
        return context

    with patch.object(git_rexec.os, "scandir", side_effect=ordered_scandir):
        assert git_rexec.git_find_repo({tmp_path}) == (set(), set())


def test_git_find_repo_prunes_marked_tree_only(tmp_path: Path) -> None:
    """Skip marked descendants while preserving an unmarked sibling repository."""
    blocked = tmp_path / "blocked"
    (blocked / "nested" / ".git").mkdir(parents=True)
    (blocked / ".git-rexec-ignore").touch()
    allowed = tmp_path / "allowed"
    (allowed / ".git").mkdir(parents=True)

    assert git_rexec.git_find_repo({tmp_path}) == ({allowed.resolve()}, set())


@pytest.mark.parametrize("marker_location", ["root", "ancestor"])
def test_git_find_repo_marked_start(tmp_path: Path, marker_location: str) -> None:
    """Honor markers at or above the starting directory."""
    root = tmp_path / "root"
    (root / "repo" / ".git").mkdir(parents=True)
    marker_parent = root if marker_location == "root" else tmp_path
    (marker_parent / ".git-rexec-ignore").touch()

    assert git_rexec.git_find_repo({root}) == (set(), set())


@pytest.mark.parametrize("inside_repository", [False, True])
@pytest.mark.parametrize("marker_location", ["start", "ancestor"])
def test_discover_repos_marked_start(
    tmp_path: Path, inside_repository: bool, marker_location: str
) -> None:
    """Honor starting markers through Git lookup and traversal entry points."""
    root = tmp_path / "root"
    start = root / "nested"
    (start / "repo" / ".git").mkdir(parents=True)
    (root / ".git").mkdir()
    marker_parent = start if marker_location == "start" else root
    (marker_parent / ".git-rexec-ignore").touch()
    backend = MagicMock(spec=git_rexec.GitBackend)
    backend.toplevel.return_value = root if inside_repository else None

    assert git_rexec.discover_repos(backend, [start], 1, [], False) == set()


@pytest.mark.parametrize("kind", ["submodule", "worktree"])
@pytest.mark.parametrize("marker_location", ["repository", "ancestor"])
def test_get_sub_repos_filters_marked_targets(
    tmp_path: Path, kind: str, marker_location: str
) -> None:
    """Exclude Git-discovered targets with direct or ancestor markers."""
    source = tmp_path / "source"
    source.mkdir()
    if kind == "worktree":
        (source / ".git").touch()
    else:
        (source / ".git").mkdir()
    (source / ".gitmodules").touch()
    blocked_parent = tmp_path / "blocked"
    blocked = blocked_parent / "repo"
    allowed = tmp_path / "allowed"
    for repo in (blocked, allowed):
        repo.mkdir(parents=True)
        (repo / ".git").touch()
    marker_parent = blocked if marker_location == "repository" else blocked_parent
    (marker_parent / ".git-rexec-ignore").touch()
    backend = MagicMock(spec=git_rexec.GitBackend)
    backend.toplevel.return_value = source
    backend.list_worktrees.return_value = (
        {blocked, allowed} if kind == "worktree" else set()
    )
    backend.list_submodules.return_value = (
        {blocked, allowed} if kind == "submodule" else set()
    )

    assert git_rexec.get_sub_repos(backend, source) == {allowed}
    expected = {allowed} if kind == "worktree" else {source, allowed}
    assert git_rexec.discover_repos(backend, [source], 2, [], True) == expected


def test_discover_repos_skips_marked_source(tmp_path: Path) -> None:
    """Avoid expanding a source repository excluded by a marker."""
    (tmp_path / ".git").touch()
    (tmp_path / ".gitmodules").touch()
    (tmp_path / ".git-rexec-ignore").touch()
    backend = MagicMock(spec=git_rexec.GitBackend)

    backend.toplevel.return_value = tmp_path

    assert (
        git_rexec.discover_repos(
            backend, [tmp_path], max_workers=1, exclude_dirs=[], include_sub_repos=True
        )
        == set()
    )
    backend.list_worktrees.assert_not_called()
    backend.list_submodules.assert_not_called()
