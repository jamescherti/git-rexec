# git-rexec: Find Git Repositories and Execute Commands Against Them, either Sequentially or in Parallel

The [git-rexec](https://github.com/jamescherti/git-rexec/) command-line tool recursively locates Git repositories within a directory and executes commands against them, either sequentially or in parallel.

Here are examples demonstrating how to use `git-rexec`:
- Execute `git status -s` across all discovered Git repositories (found by searching recursively under the current working directory) in parallel (`-p` or `--parallel`):
  ```
  git-rexec -p -- git status -s
  ```

- Fetch updates across all discovered repositories, limiting the concurrency to 5 background jobs (`-j 5`), which helps avoid network congestion or server rate limits when communicating with upstream Git remotes:
  ```bash
  git-rexec -j 5 --parallel -- git fetch
  ```


- Include sub-repositories (e.g., Git worktrees and submodules) alongside standard Git repositories during discovery (`-s` or `--include-sub-repos`), and execute `git status` against them:
  ```bash
  git-rexec -s -p -- git status
  ```

- Target a specific base directory (`~/projects`) using the `-C` flag to recursively discover repositories within it, while explicitly excluding a specific subfolder (`~/projects/archive`). This example executes `git status -s` in parallel for all discovered repositories except those within the excluded path:
  ```bash
  git-rexec -C ~/projects --exclude-dir ~/projects/archive --parallel -- git status -s
  ```

- Evaluate whether a `README.md` file exists in the repository (`sh -c "test -f README.md"`). If the condition returns an exit status of 0 (success), it counts the number of lines in that file (`wc -l README.md`):
  ```bash
  git-rexec --if-exec 'sh -c "test -f README.md"' --parallel -- wc -l README.md
  ```

- Print the paths of all discovered Git repositories:
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

## Installation

### Method 1: Manual Installation (System-wide)

Download the `git-rexec` script, make it executable, and copy it to a directory in your system PATH (e.g., `/usr/local/bin`):

```bash
sudo cp git-rexec /usr/local/bin/
```

### Method 2: Installation via pip

Install the package directly from the Git repository using `pip`:

```bash
pip install --user git-rexec
```

## Dependencies

### System Dependencies

- `git`: Required for repository validation and execution.

### Python Dependencies (Optional)

- `colorama`: Provides color-coded terminal output.
- `setproctitle`: Sets the process title for process monitoring tools.

You can install the optional Python dependencies via pip:

```bash
pip install colorama setproctitle
```

## Usage

```bash
git-rexec [OPTIONS] [exec_cmd ...]
```

*(Assuming the `git-rexec` script is executable and in your PATH.)*

### Positional Arguments

- `exec_cmd`: The shell command to execute within each discovered Git repository. You can use `--` to pass options directly to the command. If omitted, the script simply prints the paths of the discovered repositories.

### Options

- `-C, --directory <path>`: The root directory to start searching for Git repositories. Defaults to the current working directory (`.`).
- `--exclude-dir <path>`: Exclude a specific directory and all of its subdirectories from the search. This option can be provided multiple times.
- `-p, --parallel`: Execute the command in parallel using threads.
- `-i, --if-exec <command>`: Execute the main command only if this check command returns an exit code of `0`.
- `-j, --jobs <int>`: The maximum number of concurrent workers/processors to use for parallel execution. Defaults to the number of CPU cores available.
- `-s, --include-sub-repos`: Include sub-repositories (e.g., Git worktrees and submodules) in the search target list.
- `-h, --help`: Show the help message and exit.
- `-q, --quiet`: Quiet mode. Suppresses the informational tracking headers (`[EXEC]` and `[EXEC-P]`) that prefix execution output. In sequential mode, it hides the `[EXEC]` repository delimiter line entirely; in parallel mode (`-p`), it strips the yellow `[EXEC-P]` header track and removes the four-space indentation, printing only the raw, unindented stdout and stderr streams. This flag has no effect when no execution command is supplied, allowing discovered repository paths to print normally.
- `--print`: Print the paths (only when no command is provided)
- `--print0`: Separate the paths with a null character (only when no command is provided)

## License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

Copyright (C) 2019-2026 [James Cherti](https://www.jamescherti.com).

## Links

- [git-rexec @GitHub](https://github.com/jamescherti/git-rexec)
