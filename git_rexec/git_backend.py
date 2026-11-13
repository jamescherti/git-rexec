#!/usr/bin/env python
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
"""Git backend."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

try:
    import pygit2

    HAS_PYGIT2 = True
except ImportError:
    HAS_PYGIT2 = False


class GitBackend:
    """Provide low-level Git plumbing operations.

    Currently supports 'pygit2' and the 'git' command-line backend.
    """

    def __init__(self, backend: str | None = None) -> None:
        """Initialize the Git backend.

        :param backend: The backend engine to use ('pygit2' or 'git'). If None,
                        it defaults to 'pygit2' if available, otherwise 'git'.
        """
        if backend is None:
            backend = "pygit2" if HAS_PYGIT2 else "git"

        if backend == "pygit2" and not HAS_PYGIT2:
            raise ValueError("pygit2 backend requested but pygit2 is not installed.")

        if backend not in ("git", "pygit2"):
            raise ValueError(f"Unsupported Git backend: {backend}")

        self.backend: str = backend

    def toplevel(self, repo_path: Path) -> Path | None:
        """Return the absolute path to the top-level directory of a Git repository.

        :param repo_path: Path inside the Git repository.
        :return: Path to the repository's top-level directory, or None if not a
                 Git repo.
        """
        if self.backend == "pygit2":
            try:
                repo_dir = pygit2.discover_repository(str(repo_path))
                if not repo_dir:
                    return None

                repo = pygit2.Repository(repo_dir)
                if repo.is_bare or not repo.workdir:
                    return None

                return Path(repo.workdir).resolve()
            except pygit2.GitError:
                return None

        try:
            proc: subprocess.CompletedProcess[str] = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                # This allows handling Git repository paths containing filenames
                # that are not valid UTF-8. With text=True, subprocess.run() decodes
                # Git's output into a string. By default, decoding errors can raise
                # UnicodeDecodeError. Using errors="surrogateescape" provides:
                # 1. Lossless decoding: Invalid UTF-8 bytes are represented using
                # Unicode surrogate code points (U+DC80 through U+DCFF).
                # 2. Round-trip preservation: When the resulting path is passed back to
                # filesystem operations or subprocesses, Python can encode those
                # surrogate characters back into their original bytes.
                encoding=sys.getfilesystemencoding(),
                errors="surrogateescape",
                check=True,
            )
            return Path(proc.stdout.removesuffix("\n")).resolve()
        except (subprocess.CalledProcessError, OSError):
            return None

    def list_worktrees(self, resolved_repo_path: Path) -> set[Path]:
        """Return a set of non-bare worktree paths associated with a repository.

        :param resolved_repo_path: The resolved path to the repository or worktree.
        :return: A set of resolved paths to the worktrees.
        """
        worktrees: set[Path] = set()

        if self.backend == "pygit2":
            try:
                repo = pygit2.Repository(str(resolved_repo_path))
                if not repo.is_bare and repo.workdir:
                    worktrees.add(Path(repo.workdir).resolve())

                if hasattr(repo, "listall_worktrees"):
                    for wt_name in repo.listall_worktrees():
                        try:
                            wt = repo.lookup_worktree(wt_name)
                            if wt and hasattr(wt, "path") and wt.path:
                                worktrees.add(Path(wt.path).resolve())
                        except pygit2.GitError:
                            continue
            except pygit2.GitError:
                pass
            return worktrees

        # Discover worktrees
        # Git's --porcelain output can contain literal newlines inside paths. Calling
        # .splitlines() therefore corrupts those paths. -z uses Git's NUL-delimited
        # output, which is safer.
        worktree_proc: subprocess.CompletedProcess[str] = subprocess.run(
            [
                "git",
                "-C",
                str(resolved_repo_path),
                "worktree",
                "list",
                "--porcelain",
                "-z",
            ],
            capture_output=True,
            text=True,
            encoding=sys.getfilesystemencoding(),
            errors="surrogateescape",
            check=True,
        )

        current_worktree: Path | None = None
        for line in worktree_proc.stdout.split("\0"):
            if line.startswith("worktree "):
                current_worktree = Path(line[9:]).resolve()
                worktrees.add(current_worktree)
            elif line == "bare" and current_worktree:
                worktrees.discard(current_worktree)

        return worktrees

    def list_submodules(self, resolved_repo_path: Path) -> set[Path]:
        """Return a set of submodule paths for a given repository.

        :param resolved_repo_path: The resolved path to the repository.
        :return: A set of resolved paths to the submodules.
        """
        submodules: set[Path] = set()

        if self.backend == "pygit2":
            try:
                repo = pygit2.Repository(str(resolved_repo_path))
                if repo.is_bare or not repo.workdir:
                    return submodules

                workdir = Path(repo.workdir)

                if hasattr(repo, "listall_submodules"):
                    for sm_name in repo.listall_submodules():
                        try:
                            sm = repo.lookup_submodule(sm_name)  # type: ignore[attr-defined]
                            if sm and hasattr(sm, "path") and sm.path:
                                submodules.add((workdir / sm.path).resolve())
                        except pygit2.GitError:
                            continue
                elif hasattr(repo, "submodules"):
                    for sm in repo.submodules:
                        if sm and hasattr(sm, "path") and sm.path:
                            submodules.add((workdir / sm.path).resolve())
            except pygit2.GitError:
                pass
            return submodules

        sm_proc: subprocess.CompletedProcess[str] = subprocess.run(
            [
                "git",
                "-C",
                str(resolved_repo_path),
                "submodule",
                "foreach",
                "--quiet",
                # Use the running Python interpreter for NUL-delimited paths.
                shlex.join(
                    [
                        sys.executable,
                        "-c",
                        "import os, sys; sys.stdout.buffer.write("
                        "os.getcwdb() + b'\\0')",
                    ]
                ),
            ],
            capture_output=True,
            text=True,
            encoding=sys.getfilesystemencoding(),
            errors="surrogateescape",
            check=True,
        )

        for line in sm_proc.stdout.split("\0"):
            if not line:
                continue

            sm_path: Path = Path(line).resolve()
            submodules.add(sm_path)

        return submodules
