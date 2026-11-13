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
"""Provide optional terminal color support for command-line output.

Use colorama for colored terminal output when available. Otherwise, provide fallback
color attributes containing empty strings to preserve compatibility without requiring
colorama as a dependency.
"""

try:
    from colorama import Fore
    from colorama import init as colorama_init

    HAS_COLORAMA: bool = True
except ImportError:
    HAS_COLORAMA = False

    def colorama_init(
        autoreset: bool = False,
        convert: bool | None = None,
        strip: bool | None = None,
        wrap: bool = True,
    ) -> None:
        """Provide a no-op fallback when colorama is unavailable."""

    class Fore:  # type: ignore[no-redef]
        """Provide empty terminal color attributes when colorama is unavailable."""

        YELLOW: str = ""
        RED: str = ""
        RESET: str = ""
