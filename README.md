# git-rexec: Find Git Repositories and Execute Commands Against Them, either Sequentially or in Parallel
![Build Status](https://github.com/jamescherti/git-rexec/actions/workflows/ci.yml/badge.svg)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![PyPI Version](https://img.shields.io/pypi/v/git-rexec.svg)](https://pypi.org/project/git-rexec/)
![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)
![Windows](https://img.shields.io/badge/Windows-0078D6?logo=windows&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-000000?logo=apple&logoColor=white)

- Author: James Cherti <[https://www.jamescherti.com/](https://www.jamescherti.com/)>
- License: GPL 3.0 or later

The [git-rexec](https://github.com/jamescherti/git-rexec/) command-line tool recursively locates Git repositories within a directory and executes commands against them, either sequentially or in parallel.

Here are examples demonstrating how to use `git-rexec`:

Example 1: Execute `git status -s` across all discovered Git repositories (found by searching recursively under the current working directory) in parallel (`-p` or `--parallel`):
```
git-rexec -p -- git status -s
```

Example 2: Fetch updates across all discovered repositories, limiting the concurrency to 5 background jobs (`-j 5`), which helps avoid network congestion or server rate limits when communicating with upstream Git remotes:
```bash
git-rexec -j 5 --parallel -- git fetch
```

Example 3: Include sub-repositories (e.g., Git worktrees and submodules) alongside standard Git repositories during discovery (`-s` or `--include-sub-repos`), and execute `git status` against them:
```bash
git-rexec -s -p -- git status
```

Example 4: Target a specific base directory (`~/projects`) using the `-C` flag to recursively discover repositories within it, while explicitly excluding a specific subfolder (`~/projects/archive`). This example executes `git status -s` in parallel for all discovered repositories except those within the excluded path:
```bash
git-rexec -C ~/projects --exclude-dir ~/projects/archive --parallel -- git status -s
```

Example 5: Evaluate whether a `README.md` file exists in the repository (`sh -c "test -f README.md"`). If the condition returns an exit status of 0 (success), it counts the number of lines in that file (`wc -l README.md`):
```bash
git-rexec --if-exec 'sh -c "test -f README.md"' --parallel -- wc -l README.md
```

Example 6: Print the paths of all discovered Git repositories:
```bash
git-rexec --print
```

If this helps your workflow, please support the project by **⭐ starring git-rexec on GitHub** and sharing it on your website, blog, Mastodon, Reddit, X, LinkedIn, or other social media platforms to help more Git users discover its benefits.

## Features

- Recursively discover Git repositories starting from a specified root directory.
- Optional flag to include sub-repositories (e.g., Git worktrees and submodules) in the execution target list.
- Execute shell commands across multiple repositories in parallel using worker threads.
- Filter target repositories based on the exit code of a conditional check (`--if-exec`).
- Exclude specific directories from the search path.
- Export discovered repository paths for integration with other shell tools (using `--print` or `--print0`).
- Optional `pygit2` dependency speeds up discovery across large directory trees by using libgit2 bindings instead of spawning `git` subprocesses.
- Optional `colorama` dependency provides color coded terminal output for better visibility and easier parsing of parallel execution results.
- Optional `setproctitle` dependency sets the process title to `git-rexec` for easier tracking in process monitoring tools.
- Skip directory exploration and repository discovery when a .git-rexec-ignore file is present in a directory.

## Installation

Here is how to install `git-rexec` using [pip](https://pypi.org/project/pip/):

```bash
pip install --user git-rexec
```

The pip command above will install the `pathaction` executable in the directory `~/.local/bin/`.

## Optional Dependencies

The `git-rexec` CLI offers optional dependencies that extend its functionality. These extras can be installed according to environment requirements.

**Colored Terminal Output (`colors`)**: Installs `colorama` to provide consistent cross-platform ANSI color support. This enhances the readability of standard output and error messages.

```bash
pip install "git-rexec[colors]"
```

**Custom Process Title (`proctitle`)**: Installs `setproctitle` to rename the running process from `python` to `git-rexec`. This simplifies identification in system monitoring tools such as `top`, `htop`, and `ps`.

```bash
pip install "git-rexec[proctitle]"
```

To install both extras at once, use a comma-separated list:

```bash
pip install "git-rexec[colors,proctitle]"
```

## Dependencies

### System Dependencies

- `git`: Required for repository validation and execution.

### Python Dependencies (Optional)

- `colorama`: Provides color-coded terminal output.
- `setproctitle`: Sets the process title for process monitoring tools.
- `pygit2`: Speeds up discovery and execution time by using libgit2 bindings for Git operations instead of spawning `git` subprocesses.

You can install the optional Python dependencies via pip:

```bash
pip install colorama setproctitle pygit2
```

## Usage

```bash
git-rexec [OPTIONS] -- [exec_cmd ...]
```

*(Assuming the `git-rexec` script is executable and in your PATH.)*

### Positional Arguments

- `exec_cmd`: The shell command to execute within each discovered Git repository. You can use `--` to pass options directly to the command. If omitted, the script simply prints the paths of the discovered repositories.

### Options

```
usage: git-rexec [-h] [-C DIRECTORY] [--exclude-dir EXCLUDE_DIR] [-p] [-i IF_EXEC] [-j MAX_WORKERS] [-q] [-s] [--print] [--print0] [exec_cmd ...]

Find Git repositories and execute commands against them in parallel.

positional arguments:
  exec_cmd              The command to execute. You can use -- to pass options.

options:
  -h, --help            show this help message and exit
  -C, --directory DIRECTORY
                        Root directory to search (defaults to current directory)
  --exclude-dir EXCLUDE_DIR
                        Exclude a specific directory and all of its subdirectories
  -p, --parallel        Execute the command in parallel using threads
  -i, --if-exec IF_EXEC
                        Execute commands only if this check returns exit code 0.
  -j, --jobs MAX_WORKERS
                        Maximum number of processors/workers to use
  -q, --quiet           Quiet mode. Suppresses the informational log prefixes ([EXEC] and [EXEC-P]) that precede execution output.
  -s, --include-sub-repos
                        Include sub-repositories (e.g., Git worktrees and submodules)
  --print               Print the paths (only when no command is provided)
  --print0              Separate the paths with a null character (only when no command is provided)
```

## Frequently Asked Questions

### How to exclude directories?

The `git-rexec` utility skips directory trees during repository discovery through:

- **Explicit Exclusions:** Passing paths via the `--exclude` command line option bypasses traversal for those target directories and their contents.
- **Marker Files:** File system tree scanning stops immediately when a `.git-rexec-ignore` file is found inside a directory, skipping all nested subdirectories.

This behavior allows developers to filter out specific folder hierarchies, such as vendor trees, archives, or external dependencies, either automatically via marker files or explicitly through command line arguments.

### Why use git-rexec instead of find and xargs?

It is possible to use `find` and `xargs` to achieve a flexible pipeline. For example, to discover all Git repositories starting from the current directory and run `git status` in parallel, one could use:

```bash
find . \
    -type d \
    -exec bash -c '[[ "$(git -C "$1" rev-parse --is-inside-work-tree 2>/dev/null)" == true ]]' '' {} \; \
    -prune \
    -print0 \
| xargs -0 -P10 -I{} git -C {} status
```

While standard shell utilities like `find` and `xargs` are highly capable, `git-rexec` addresses performance limitations and edge cases in shell pipelines, reducing complex commands to a single command:

```bash
git-rexec -p -j 10 -- git status
```

The `git-rexec` command-line tool offers significant performance improvements. A traditional `find -exec bash -c 'git ...'` spawns separate processes for every directory visited during discovery. By avoiding this, `git-rexec` is faster. According to the author's benchmark, `git-rexec` is 4.36 times faster than the `find` and `xargs` command.

Additionally, `git-rexec` improves output readability. Standard `xargs` makes it difficult to trace output back to a specific repository when operating concurrently. The `git-rexec` tool automatically prefixes all output with the corresponding repository context, adding indentation and optional colors during parallel execution. It also executes non-Git commands directly within the repository root, removing the need for `sh -c 'cd "$1" && ...'` wrappers, and includes built-in arguments for handling sub-repositories (`-s`), exclusions (`--exclude-dir`), and conditional execution (`--if-exec`).

Furthermore, `git-rexec` handles nested repository edge cases natively. Using `find` with `git rev-parse --is-inside-work-tree` often fails within umbrella repositories containing nested clones. The shell evaluation triggers a prune at the root directory, preventing the discovery of the nested repositories. The `git-rexec` command parses these structures correctly by default.

For workflows requiring strict separation between discovery and execution, or to cache a static list of repositories, `git-rexec` provides the `--print` and `--print0` arguments:

```bash
git-rexec --print0 | xargs -0 -P10 -I{} git -C {} fetch --verbose
```

## License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

Copyright (C) 2019-2026 [James Cherti](https://www.jamescherti.com).

## Links

- [git-rexec @GitHub](https://github.com/jamescherti/git-rexec)
