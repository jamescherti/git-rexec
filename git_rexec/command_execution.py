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
"""Low-level Git plumbing operations."""

import os
import shutil
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

EXIT_COMMAND_NOT_FOUND: int = 127
EXIT_CANNOT_EXECUTE: int = 126


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


def command_exists(command: str, cwd: Path | None = None) -> bool:
    """Check whether a command exists, optionally relative to a directory."""
    if not command:
        return False

    # The current working directory has to exist
    cwd_path = cwd.absolute() if cwd else Path.cwd()
    if not cwd_path.is_dir():
        return False

    cmd_path = Path(command)

    # Relative path with a separator (e.g., "./script.sh" or "bin/run"): Resolve it
    # against cwd_path so shutil.which() checks the file inside the target directory
    # instead of the current working directory.
    if not cmd_path.is_absolute() and (
        (os.sep in command) or (os.altsep and os.altsep in command)
    ):
        command = str(cwd_path / cmd_path)

    return bool(shutil.which(command))


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
        if not command_exists(cmd_list[0], cwd=repo_path):
            raise FileNotFoundError(cmd_list[0])
        proc: subprocess.CompletedProcess[str] = subprocess.run(
            cmd_list,
            cwd=repo_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
            text=True,
            encoding=sys.getfilesystemencoding(),
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
                raise RuntimeError(
                    f"Error processing repository {repo_path}: {exc}"
                ) from exc

    results.sort(key=lambda ctx: ctx.path)
    return results
