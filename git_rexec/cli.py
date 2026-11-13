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
from multiprocessing import cpu_count
from pathlib import Path

from .colors import HAS_COLORAMA, Fore, colorama_init
from .command_execution import (
    EXIT_CANNOT_EXECUTE,
    EXIT_COMMAND_NOT_FOUND,
    CommandResult,
    RepoContext,
    command_exists,
    execute_parallel_tasks,
)
from .git_backend import GitBackend
from .git_discovery import discover_repos, format_parallel_output

try:
    from setproctitle import setproctitle

    HAS_SETPROCTITLE: bool = True
except ImportError:
    HAS_SETPROCTITLE = False


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


def command_line_interface() -> None:
    """Entry point with signal and pipe exception handling."""
    try:
        _run()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(141)
