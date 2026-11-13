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
"""Find Git repositories and execute commands against them in parallel.

This script allows for finding Git repositories within a directory structure and
executing commands against them. It supports conditional filtering, background
execution, and sequential foreground execution.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import textwrap
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
from pathlib import Path

from .command_execution import (
    EXIT_CANNOT_EXECUTE,
    EXIT_COMMAND_NOT_FOUND,
    CommandResult,
    RepoContext,
    command_exists,
    execute_parallel_tasks,
)
from .git_backend import GitBackend

GIT_REXEC_IGNORE_FILE: str = ".git-rexec-ignore"

try:
    from colorama import Fore
    from colorama import init as colorama_init

    HAS_COLORAMA: bool = True
except ImportError:
    HAS_COLORAMA = False

    class Fore:  # type: ignore
        """Provide fallback empty strings for missing colorama Fore attributes."""

        YELLOW: str = ""
        RED: str = ""
        RESET: str = ""


try:
    from setproctitle import setproctitle

    HAS_SETPROCTITLE: bool = True
except ImportError:
    HAS_SETPROCTITLE = False


def is_excluded(
    resolved_target: Path,
    resolved_exclude_paths: set[Path] | list[Path] | None,
) -> bool:
    """Check if a resolved target path is within the resolved excluded paths.

    :param resolved_target: The resolved path to check.
    :param resolved_exclude_paths: A collection of resolved paths to exclude.
    :return: True if the target is excluded, False otherwise.
    """
    if not resolved_exclude_paths:
        return False

    return any(
        resolved_target == ex or resolved_target.is_relative_to(ex)
        for ex in resolved_exclude_paths
    )


def has_ignore_marker(directory: Path) -> bool:
    """Check whether a directory contains an ignore marker.

    Symbolic links to regular files are treated as ignore markers.

    :param directory: Directory path to check.
    :return: True if the marker resolves to a regular file.
    """
    return (directory / GIT_REXEC_IGNORE_FILE).is_file()


def is_ignored_directory(directory: Path) -> bool:
    """Check for a regular ignore marker in a directory or its ancestors.

    :param directory: Absolute directory path to check.
    :return: True if a regular marker excludes the directory tree.
    """
    return has_ignore_marker(directory) or any(
        has_ignore_marker(parent) for parent in directory.parents
    )


def git_find_repo(
    resolved_roots: Iterable[Path],
    resolved_exclude_paths: set[Path] | list[Path] | None = None,
) -> tuple[set[Path], set[Path]]:
    """Recursively find all Git repositories under the supplied roots.

    Overlapping scan roots are handled without scanning the same directory
    multiple times. Explicitly supplied nested roots are preserved even
    when their parent directories are Git repositories.

    :param resolved_roots: A collection of resolved directories to scan together.
    :param resolved_exclude_paths: Resolved directories to exclude to optimize
        traversal.
    :return: A tuple containing standard repository paths and worktrees paths.
    """
    # Filter out scan roots that are explicitly excluded or contain an ignore marker in
    # their directory hierarchy, preventing unnecessary traversal.
    eligible_scan_roots: set[Path] = {
        directory
        for directory in resolved_roots
        if not is_excluded(directory, resolved_exclude_paths)
        and not is_ignored_directory(directory)
    }

    # Preserve explicitly supplied roots, including nested roots, while preventing their
    # discovery through parent roots from causing duplicate directory scans.
    dirs_to_visit: set[Path] = eligible_scan_roots.copy()

    git_repos: set[Path] = set()
    git_worktrees: set[Path] = set()

    while dirs_to_visit:
        current_dir: Path = dirs_to_visit.pop()

        # Try and OSError It protects your script from crashing mid-execution when
        # encountering common filesystem issues. In a directory scanner, os.scandir()
        # and entry checks frequently raise exceptions that inherit from OSError. For
        # example, Permission Denied, Broken Links and Race Conditions, I/O errors...
        try:
            # os.DirEntry provides efficient filesystem checks using cached metadata,
            # avoiding unnecessary filesystem calls and Path object creation.
            with os.scandir(current_dir) as iter_scandir:
                dirs_in_current: list[Path] = []
                is_repo: bool = False

                # Scan directories
                for entry in iter_scandir:
                    try:
                        if entry.name == ".git":
                            if entry.is_dir(follow_symlinks=False):
                                # Found the repository, stop evaluating this directory
                                is_repo = True
                                git_repos.add(current_dir)
                                break
                            if entry.is_file(follow_symlinks=False):
                                # Found the worktree, stop evaluating this directory
                                is_repo = True
                                git_worktrees.add(current_dir)
                                break
                        # Add the directory to dirs_in_current to scan it recursively
                        elif entry.is_dir(follow_symlinks=False):
                            entry_path: Path = Path(entry.path)
                            # Skip excluded or ignored directories, as well as
                            # explicitly supplied scan roots that are already scheduled,
                            # to prevent redundant directory scans.
                            if (
                                entry_path in eligible_scan_roots
                                or is_excluded(entry_path, resolved_exclude_paths)
                                or has_ignore_marker(entry_path)
                            ):
                                # Excluded or already scheduled as a scan root
                                continue

                            dirs_in_current.append(entry_path)
                    except OSError:
                        continue

                if not is_repo and dirs_in_current:
                    # Append collected directories only if we are not inside a
                    # repository
                    dirs_to_visit.update(dirs_in_current)
        except OSError:
            continue

    return git_repos, git_worktrees


def get_sub_repos(
    git_backend: GitBackend,
    resolved_repo_path: Path,
    resolved_exclude_paths: set[Path] | list[Path] | None = None,
) -> set[Path]:
    """Extract worktrees and submodules from a Git repository or worktree.

    :param git_backend: The GitBackend instance for plumbing operations.
    :param resolved_repo_path: The path to the standard repository or worktree.
    :param resolved_exclude_paths: Resolved directories to exclude.
    :return: A set of absolute paths to worktrees and submodules.
    """
    sub_repos: set[Path] = set()

    if (resolved_repo_path / ".git").is_file():
        for current_worktree in git_backend.list_worktrees(resolved_repo_path):
            # Exclude the current repo path to prevent self-referencing
            if (
                current_worktree != resolved_repo_path
                and current_worktree.is_dir()
                and (current_worktree / ".git").exists()
                and not is_excluded(current_worktree, resolved_exclude_paths)
                and not is_ignored_directory(current_worktree)
            ):
                sub_repos.add(current_worktree)

    # Discover submodules
    if (resolved_repo_path / ".gitmodules").is_file():
        for sm_path in git_backend.list_submodules(resolved_repo_path):
            if (
                sm_path != resolved_repo_path
                and sm_path.is_dir()
                and not is_excluded(sm_path, resolved_exclude_paths)
                and not is_ignored_directory(sm_path)
            ):
                sub_repos.add(sm_path)

    return sub_repos


def format_parallel_output(
    repo_path: Path, result: CommandResult, quiet: bool = False
) -> str:
    """Format the output of a parallel command for display.

    :param repo_path: The repository path.
    :param result: The execution result.
    :param quiet: Suppress informational headers.
    :return: Formatted string ready for printing.
    """
    raw_output: str = result.stdout + result.stderr
    if not raw_output and result.returncode == 0:
        return ""

    if quiet:
        return raw_output

    formatted: str = raw_output.replace("\t", "    ").rstrip()
    if formatted:
        formatted += "\n"

    cmd_str: str = " ".join(result.command)
    header: str = f"{Fore.YELLOW}[EXEC-P] {repo_path}: {cmd_str}{Fore.RESET}\n"
    indented_body: str = textwrap.indent(formatted, prefix="    ")
    return header + indented_body


def discover_repos(
    git_backend: GitBackend,
    directories: Iterable[Path],
    max_workers: int,
    exclude_dirs: list[str],
    include_sub_repos: bool,
) -> set[Path]:
    """Discover Git repositories across all scan roots in one traversal.

    :param git_backend: The GitBackend instance for plumbing operations.
    :param directories: One or more directories to search.
    :param max_workers: Maximum number of threads/workers.
    :param exclude_dirs: Directories to exclude.
    :param include_sub_repos: Whether to include sub-repositories.
    :return: A set of repository root paths.
    """
    resolved_ex_dirs: set[Path] = {Path(ex_dir).resolve() for ex_dir in exclude_dirs}

    resolved_scan_roots: set[Path] = set()
    for directory in {path.resolve() for path in directories}:
        if is_excluded(directory, resolved_ex_dirs) or is_ignored_directory(directory):
            continue

        resolved_scan_roots.add(git_backend.toplevel(directory) or directory)

    # Find repositories and worktrees
    git_repos, git_worktrees = git_find_repo(resolved_scan_roots, resolved_ex_dirs)

    # Extract and append sub-repositories concurrently if requested
    if include_sub_repos:
        git_repos.update(git_worktrees)
        candidates: set[Path] = git_repos.copy()
        if candidates:
            # Recursively get sub repos from sub repos
            visited: set[Path] = set()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                while candidates:
                    visited.update(candidates)
                    # We must search for worktrees/submodules in BOTH repos and
                    # worktrees
                    futures: list[Future[set[Path]]] = [
                        executor.submit(
                            get_sub_repos, git_backend, repo, resolved_ex_dirs
                        )
                        for repo in candidates
                    ]

                    for future in as_completed(futures):
                        git_repos.update(future.result())

                    candidates = git_repos - visited

        # Remove worktrees from the list
        git_repos -= git_worktrees

    return git_repos


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    :return: Parsed arguments.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=(
            "Find Git repositories and execute commands against them in parallel."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        action="append",
        dest="directories",
        metavar="DIRECTORY",
        help="Root directory to search (repeatable, defaults to the current directory)",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Exclude a specific directory and all of its subdirectories",
    )
    parser.add_argument(
        "-p",
        "--parallel",
        action="store_true",
        help="Execute the command in parallel using threads",
        default=False,
    )
    parser.add_argument(
        "-i",
        "--if-exec",
        type=str,
        help="Execute commands only if this check returns exit code 0.",
        default=None,
    )
    parser.add_argument(
        "-j",
        "--jobs",
        # If someone passes -j <= 0, max_workers will be set to 1
        type=lambda x: max(1, int(x)),
        dest="max_workers",
        help="Maximum number of processors/workers to use",
        default=max(1, cpu_count() or 1),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "Quiet mode. Suppresses the informational log prefixes "
            "([EXEC] and [EXEC-P]) that precede execution output."
        ),
        default=False,
    )
    parser.add_argument(
        "-s",
        "--include-sub-repos",
        action="store_true",
        help="Include sub-repositories (e.g., Git worktrees and submodules)",
        default=False,
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the paths (only when no command is provided)",
        default=False,
    )
    parser.add_argument(
        "--print0",
        action="store_true",
        help=(
            "Separate the paths with a null character (only when no "
            "command is provided)"
        ),
        default=False,
    )
    parser.add_argument(
        "exec_cmd",
        type=str,
        nargs="*",
        help="The command to execute. You can use -- to pass options.",
    )

    args: argparse.Namespace = parser.parse_args()

    if args.max_workers < 1:
        parser.error("--jobs must be at least 1")

    return args


def print_error_summary(errors: list[tuple[Path, CommandResult]]) -> int:
    """Print a summary of execution errors.

    :param errors: list of tuples containing path and result.
    :return: The final exit code (1 if errors exist, else 0).
    """
    if not errors:
        return 0

    print()
    print(f"{Fore.RED}Errors:{Fore.RESET}")

    final_errno: int = 0
    for repo_path, result in errors:
        cmd_display: str = " ".join(result.command)
        if result.returncode != 0:
            final_errno = 1
            if result.returncode == EXIT_COMMAND_NOT_FOUND:
                msg: str = "Command not found"
            else:
                msg = f"errno {result.returncode}"

            print(f"{Fore.RED}  - {repo_path}: {msg}: {cmd_display}{Fore.RESET}")

    return final_errno


def _run() -> None:
    """Execute the main command-line interface."""
    if HAS_SETPROCTITLE:
        setproctitle(Path(sys.argv[0]).name)  # type: ignore

    if HAS_COLORAMA:
        colorama_init()

    # Disable git prompting
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    args: argparse.Namespace = parse_args()

    # Check for empty action
    if not args.exec_cmd and not args.print and not args.print0:
        print("Nothing to do.", file=sys.stderr)
        sys.exit(2)

    directories: list[Path] = args.directories or [Path.cwd()]
    for directory in directories:
        if not directory.is_dir():
            print(f"Error: Directory not found: '{directory}'", file=sys.stderr)
            sys.exit(1)

    if_exec_cmd: list[str] | None = None
    if args.if_exec:
        try:
            if_exec_cmd = shlex.split(args.if_exec)
        except ValueError as err:
            print(f"Error parsing --if-exec: {err}", file=sys.stderr)
            sys.exit(2)
        if not if_exec_cmd:
            print("Error: --if-exec command cannot be empty", file=sys.stderr)
            sys.exit(2)

    git_backend = GitBackend(backend="git")

    # Discover Repositories
    try:
        repos: set[Path] = discover_repos(
            git_backend,
            directories,
            args.max_workers,
            args.exclude_dir,
            args.include_sub_repos,
        )
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"Error discovering repositories: {err}", file=sys.stderr)
        sys.exit(1)

    # setup background command list if needed
    exec_parallel_cmd: list[str] | None = (
        args.exec_cmd if (args.parallel and args.exec_cmd) else None
    )

    # Parallel Processing (Filter + Background Exec)
    if not exec_parallel_cmd and not if_exec_cmd:
        processed_repos: list[RepoContext] = [
            RepoContext(path=repo) for repo in sorted(repos)
        ]
    else:
        try:
            processed_repos = execute_parallel_tasks(
                repos, exec_parallel_cmd, if_exec_cmd, args.max_workers
            )
        except RuntimeError as err:
            print(err, file=sys.stderr)
            sys.exit(1)

    execution_errors: list[tuple[Path, CommandResult]] = []
    fg_cmd_str: str = ""
    if args.exec_cmd and not args.parallel and not args.quiet:
        fg_cmd_str = (
            subprocess.list2cmdline(args.exec_cmd)
            if os.name == "nt"
            else shlex.join(args.exec_cmd)
        )

    # Main Loop: Display results and run sequential commands
    for context in processed_repos:
        repo_path: Path = context.path

        # Handle Background Execution Results
        if context.parallel_result:
            output_display: str = format_parallel_output(
                repo_path, context.parallel_result, args.quiet
            )
            if output_display:
                print(output_display, end="")

            if context.parallel_result.returncode != 0:
                execution_errors.append((repo_path, context.parallel_result))

        # Handle Foreground Execution
        if args.exec_cmd and not args.parallel:
            if not args.quiet:
                print(
                    f"{Fore.YELLOW}[EXEC] {repo_path}: {fg_cmd_str}{Fore.RESET}",
                    flush=True,
                )

            # Run interactively/sequentially (capture=False allows interaction)
            # However, for consistency with error tracking, we might want to
            # capture. Usually --exec implies seeing output immediately.
            try:
                # We use subprocess.check_call to allow direct stdout/stderr
                # flow unless we want to capture for error summary. To match
                # previous logic, we let it flow to stdout.
                if not command_exists(args.exec_cmd[0], cwd=repo_path):
                    raise FileNotFoundError(args.exec_cmd[0])
                subprocess.check_call(args.exec_cmd, cwd=repo_path)
            except subprocess.CalledProcessError as err:
                # Construct a dummy result for the error summary
                failed_res: CommandResult = CommandResult(
                    command=args.exec_cmd, returncode=err.returncode
                )
                execution_errors.append((repo_path, failed_res))
            except FileNotFoundError:
                failed_res = CommandResult(
                    command=args.exec_cmd, returncode=EXIT_COMMAND_NOT_FOUND
                )
                execution_errors.append((repo_path, failed_res))
            except OSError:
                failed_res = CommandResult(
                    command=args.exec_cmd, returncode=EXIT_CANNOT_EXECUTE
                )
                execution_errors.append((repo_path, failed_res))

        # If no commands were run, just list the repo
        if not args.exec_cmd:
            if args.print0:
                print(repo_path, end="\0")
            elif args.print:
                print(repo_path)

    # Final Error Summary
    sys.exit(print_error_summary(execution_errors))


def main() -> None:
    """Entry point with signal and pipe exception handling."""
    try:
        _run()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(141)


if __name__ == "__main__":
    main()
