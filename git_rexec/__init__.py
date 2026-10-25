#!/usr/bin/env python3
#
# Optional dependencies:
#   - colorama
#   - setproctitle
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
"""Find Git repositories and execute commands against them in parallel.

This script allows for finding Git repositories within a directory structure
and executing commands against them. It supports conditional filtering,
background execution, and sequential foreground execution.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from multiprocessing import cpu_count
from pathlib import Path

EXIT_COMMAND_NOT_FOUND: int = 127
EXIT_CANNOT_EXECUTE: int = 126

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


@dataclass
class CommandResult:
    """Store the result of a subprocess command execution.

    :param command: The command that was executed.
    :param returncode: The exit status of the command.
    :param stdout: Standard output content.
    :param stderr: Standard error content.
    """

    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class RepoContext:
    """Represent a processed Git repository and its execution states.

    :param path: The absolute path to the repository root.
    :param parallel_result: The result of the parallel execution, if any.
    """

    path: Path
    parallel_result: CommandResult | None = None


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


def git_toplevel(repo_path: Path) -> Path | None:
    """Return the absolute path to the top-level directory of a Git repository.

    :param repo_path: Path inside the Git repository.
    :return: Path to the repository's top-level directory, or None if not a
             Git repo.
    """
    try:
        proc: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            # This allows handling Git repository paths containing filenames
            # that are not valid UTF-8. With text=True, subprocess.run() decodes Git's
            # output into a string. By default, decoding errors can raise
            # UnicodeDecodeError. Using errors="surrogateescape" provides:
            # 1. Lossless decoding: Invalid UTF-8 bytes are represented using Unicode
            # surrogate code points (U+DC80 through U+DCFF).
            # 2. Round-trip preservation: When the resulting path is passed back to
            # filesystem operations or subprocesses, Python can encode those surrogate
            # characters back into their original bytes.
            errors="surrogateescape",
            check=True,
        )
        return Path(proc.stdout.removesuffix("\n")).resolve()
    except (subprocess.CalledProcessError, OSError):
        return None


def git_find_repo(
    root: Path, exclude_paths: set[Path] | list[Path] | None = None
) -> tuple[set[Path], set[Path]]:
    """Recursively find all Git repositories under the root path using Python.

    :param root: Directory to search under.
    :param exclude_paths: Directories to exclude to optimize traversal.
    :return: A tuple containing standard repository paths and worktrees
    paths.
    """
    git_repos: set[Path] = set()
    git_worktrees: set[Path] = set()

    # Resolve excluded paths once
    resolved_excludes: set[Path] = (
        {ex_path.resolve() for ex_path in exclude_paths} if exclude_paths else set()
    )

    resolved_root: Path = root.resolve()

    # Do not begin traversal if the root itself is excluded
    if is_excluded(resolved_root, resolved_excludes):
        return git_repos, git_worktrees

    dirs_to_visit: list[Path] = [resolved_root]
    while dirs_to_visit:
        current_dir: Path = dirs_to_visit.pop()

        try:
            with os.scandir(current_dir) as iter_scandir:
                dirs_in_current: list[Path] = []
                is_repo: bool = False

                # Scan directories
                for entry in iter_scandir:
                    try:
                        if entry.name == ".git":
                            # TODO Check toplevel?
                            # if git_toplevel(current_dir) != current_dir:
                            #     continue
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
                            if is_excluded(entry_path, resolved_excludes):
                                # Excluded
                                continue

                            dirs_in_current.append(entry_path)
                    except OSError:
                        continue

                if not is_repo and dirs_in_current:
                    # Append collected directories only if we are not inside a
                    # repository
                    dirs_to_visit.extend(dirs_in_current)
        except OSError:
            continue

    return git_repos, git_worktrees


def get_sub_repos(
    resolved_repo_path: Path,
    resolved_exclude_paths: set[Path] | list[Path] | None = None,
) -> set[Path]:
    """Extract worktrees and submodules from a Git repository or worktree.

    :param resolved_repo_path: The path to the standard repository or worktree.
    :param resolved_exclude_paths: Resolved directories to exclude.
    :return: A set of absolute paths to worktrees and submodules.
    """
    sub_repos: set[Path] = set()

    # Discover worktrees
    if (resolved_repo_path / ".git").is_file():
        worktree_proc: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "-C", str(resolved_repo_path), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            check=True,
        )

        current_worktree: Path | None = None
        for line in worktree_proc.stdout.splitlines():
            line = line.strip()

            if line.startswith("worktree "):
                current_worktree = Path(line[9:]).resolve()
                # Exclude the current repo path to prevent self-referencing
                if (
                    current_worktree != resolved_repo_path
                    and current_worktree.is_dir()
                    and not is_excluded(current_worktree, resolved_exclude_paths)
                ):
                    sub_repos.add(current_worktree)
            elif line == "bare" and current_worktree:
                sub_repos.discard(current_worktree)

        return sub_repos

    # Discover submodules
    if (resolved_repo_path / ".gitmodules").is_file():
        sm_proc: subprocess.CompletedProcess[str] = subprocess.run(
            [
                "git",
                "-C",
                str(resolved_repo_path),
                "submodule",
                "foreach",
                "--quiet",
                "pwd",
            ],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            check=True,
        )

        for line in sm_proc.stdout.splitlines():
            sm_path: Path = Path(line).resolve()
            if (
                sm_path != resolved_repo_path
                and sm_path.is_dir()
                and not is_excluded(sm_path, resolved_exclude_paths)
            ):
                sub_repos.add(sm_path)

    return sub_repos


def run_command_get_output(
    repo_path: Path, cmd_list: list[str], capture: bool = True
) -> CommandResult:
    """Execute a shell command within a specific directory.

    :param repo_path: The directory in which to execute the command.
    :param cmd_list: The command list to execute.
    :param capture: Whether to capture stdout/stderr.
    :return: The result of the command execution.
    """
    if not cmd_list:
        return CommandResult(command=[], returncode=0)

    try:
        proc: subprocess.CompletedProcess[str] = subprocess.run(
            cmd_list,
            cwd=repo_path,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
            text=True,
            errors="surrogateescape",
            check=False,
        )
        return CommandResult(
            command=cmd_list,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except FileNotFoundError:
        return CommandResult(
            command=cmd_list,
            returncode=EXIT_COMMAND_NOT_FOUND,
            stderr=f"Error: Command not found: '{cmd_list[0]}'\n",
        )
    except OSError as err:
        return CommandResult(
            command=cmd_list,
            returncode=EXIT_CANNOT_EXECUTE,
            stderr=f"Error: Cannot execute '{cmd_list[0]}': {err}\n",
        )


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


def process_repo(
    repo_path: Path, exec_parallel_cmd: list[str] | None, if_exec_cmd: list[str] | None
) -> RepoContext | None:
    """Process a single repository: check conditions and run parallel commands.

    :param repo_path: The repository path.
    :param exec_parallel_cmd: Command list to execute in background/parallel.
    :param if_exec_cmd: Command list to check before processing (filter).
    :return: RepoContext if processed successfully, None if filtered out.
    """
    # Filter: if-exec
    if if_exec_cmd:
        # We discard output for the filter check, only caring about exit code
        filter_res: CommandResult = run_command_get_output(
            repo_path, if_exec_cmd, capture=False
        )
        if filter_res.returncode != 0:
            return None

    # Action: exec-parallel
    parallel_result: CommandResult | None = None
    if exec_parallel_cmd:
        parallel_result = run_command_get_output(
            repo_path, exec_parallel_cmd, capture=True
        )

    return RepoContext(path=repo_path, parallel_result=parallel_result)


def discover_repos(
    directory: Path, max_workers: int, exclude_dirs: list[str], include_sub_repos: bool
) -> set[Path]:
    """Discover Git repositories starting from directory and apply exclusions.

    :param directory: The root directory for search.
    :param max_workers: Maximum number of threads/workers.
    :param exclude_dirs: Directories to exclude.
    :param include_sub_repos: Whether to include sub-repositories.
    :return: A set of repository root paths.
    """
    set_ex_paths: set[Path] = (
        {Path(ex_dir).resolve() for ex_dir in exclude_dirs} if exclude_dirs else set()
    )

    # Discover all repositories
    toplevel: Path | None = git_toplevel(directory)

    git_repos: set[Path] = set()
    git_worktrees: set[Path] = set()
    if toplevel:
        git_repos = {toplevel}
    else:
        git_repos, git_worktrees = git_find_repo(directory, set_ex_paths)

    # Extract and append sub-repositories concurrently if requested
    if include_sub_repos:
        candidates: set[Path] = git_repos | git_worktrees
        if candidates:
            # TODO recursively get sub repos from sub repos
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # We must search for worktrees/submodules in BOTH repos and worktrees
                futures: list[Future[set[Path]]] = [
                    executor.submit(get_sub_repos, repo, set_ex_paths)
                    for repo in candidates
                ]
                for future in as_completed(futures):
                    git_repos.update(future.result())

    # Filter out excluded directories (applies dynamically to sub-repos too)
    if not set_ex_paths:
        return git_repos

    return {repo for repo in git_repos if not is_excluded(repo, set_ex_paths)}


def execute_parallel_tasks(
    repos: set[Path],
    exec_parallel: list[str] | None,
    if_exec: list[str] | None,
    max_workers: int,
) -> list[RepoContext]:
    """Run discovery and parallel execution tasks.

    :param repos: set of repositories to process.
    :param exec_parallel: Command list for background execution.
    :param if_exec: Command list for conditional filtering.
    :param max_workers: Maximum number of threads for execution.
    :return: list of processed repository contexts.
    """
    if not repos:
        return []

    results: list[RepoContext] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[Future[RepoContext | None], Path] = {
            executor.submit(process_repo, repo, exec_parallel, if_exec): repo
            for repo in repos
        }

        for future in as_completed(futures):
            repo_path: Path = futures[future]
            try:
                result: RepoContext | None = future.result()
                if result:
                    results.append(result)
            except (OSError, ValueError) as exc:
                print(
                    f"Error processing repository {repo_path}: {exc}", file=sys.stderr
                )

    results.sort(key=lambda ctx: ctx.path)
    return results


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
        default=Path(),
        help="Root directory to search (defaults to current directory)",
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

    if not args.directory.is_dir():
        print(
            f"Error: Directory not found: '{args.directory}'",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify that the execution command exists
    if args.exec_cmd and not shutil.which(args.exec_cmd[0]):
        print(f"Error: Command not found: '{args.exec_cmd[0]}'", file=sys.stderr)
        sys.exit(EXIT_COMMAND_NOT_FOUND)

    if_exec_cmd: list[str] | None = None
    if args.if_exec is not None:
        try:
            if_exec_cmd = shlex.split(args.if_exec)
        except ValueError as err:
            print(f"Error parsing --if-exec: {err}", file=sys.stderr)
            sys.exit(2)
        if not if_exec_cmd:
            print("Error: --if-exec command cannot be empty", file=sys.stderr)
            sys.exit(2)
        if not shutil.which(if_exec_cmd[0]):
            print(f"Error: Command not found: '{if_exec_cmd[0]}'", file=sys.stderr)
            sys.exit(EXIT_COMMAND_NOT_FOUND)

    # Discover Repositories
    try:
        repos: set[Path] = discover_repos(
            args.directory.absolute(),
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
        processed_repos = execute_parallel_tasks(
            repos, exec_parallel_cmd, if_exec_cmd, args.max_workers
        )

    execution_errors: list[tuple[Path, CommandResult]] = []
    fg_cmd_str: str = (
        shlex.join(args.exec_cmd)
        if (args.exec_cmd and not args.parallel and not args.quiet)
        else ""
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
