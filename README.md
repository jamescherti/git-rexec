# git-rexec: Find Git Repositories and Execute Commands Against Them, either Sequentially or in Parallel

The [git-rexec](https://github.com/jamescherti/git-rexec/) tool locates Git repositories within a directory structure and executes commands against them, either sequentially or in parallel.

For example:
- Fetch updates in parallel (`-p` or `--parallel`) across all discovered Git repositories, processing 5 repositories at a time (`-j 5`):
  ```bash
  git-rexec -j 5 --parallel -- git fetch
  ```

- Check the status of all discovered Git repositories in parallel (`-p` or `--parallel`):
  ```
  git-rexec -p -- git status
  ```

- Push local changes across all discovered Git repositories in parallel, processing 5 repositories at a time:
  ```bash
  git-rexec -j 5 --parallel -- git push
  ```

## Features

- Recursively discover Git repositories starting from a specified root directory.
- Execute shell commands across multiple repositories in parallel using worker threads.
- Filter target repositories based on the exit code of a conditional check (`--if-exec`).
- Exclude specific directories from the search path.
- Optional: Can leverage `fd` for fast directory traversal if installed, falling back to standard Python path resolution otherwise.

## Installation

### Method 1: Manual Installation (System-wide)

Download the `git-rexec` script, make it executable, and copy it to a directory in your system PATH (e.g., `/usr/local/bin`):

```bash
chmod +x git-rexec
sudo cp git-rexec /usr/local/bin/
```

### Method 2: Installation via pip

Install the package directly from the Git repository using `pip`:

```bash
pip install --user git+https://github.com/jamescherti/git-rexec
```

## Dependencies

### System Dependencies

- `git`: Required for repository validation and execution.
- `fd` (Optional): Highly recommended for faster repository discovery.

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
- `-h, --help`: Show the help message and exit.
- `-q, --quiet`: Quiet mode. Suppresses the informational tracking headers (`[EXEC]` and `[EXEC-P]`) that prefix execution output. In sequential mode, it hides the `[EXEC]` repository delimiter line entirely; in parallel mode (`-p`), it strips the yellow `[EXEC-P]` header track and removes the four-space indentation, printing only the raw, unindented stdout and stderr streams. This flag has no effect when no execution command is supplied, allowing discovered repository paths to print normally.

## Examples

List all Git repositories in the current directory tree:

```bash
git-rexec
```

Fetch updates for all repositories in parallel:

```bash
git-rexec --parallel -- git fetch --all
```

Execute a command in a specific directory while excluding another:

```bash
git-rexec -C ~/projects --exclude-dir ~/projects/archive --parallel -- git status
```

Clean all repositories using a specific number of threads:

```bash
git-rexec  -j 4 --parallel -- git clean -fdx
```

## License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

Copyright (C) 2019-2026 [James Cherti](https://www.jamescherti.com).

## Links

- [git-rexec @GitHub](https://github.com/jamescherti/git-rexec)
