"""The evidence runner must not hand the MCP server's stdin to its children.

The server's stdin is the MCP stdio transport. A child that inherits fd 0
shares the server's open file description, so a child that sets O_NONBLOCK on
its stdin (Node does, and so does every npm-driven evidence command) sets it
on the server's transport too. The server's next blocking read then fails and
the server exits, which the client sees as a disconnect after a long sweep.

Each test swaps the parent's fd 0 for a pipe for the duration of the call, so
the result does not depend on what stdin pytest itself was started with.
"""

from __future__ import annotations

import fcntl
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from foundry_mcp.tools.worktree_helpers import _run_command_with_timeout


@contextmanager
def _stdin_is_a_pipe():
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)


def test_child_stdin_is_dev_null(tmp_path: Path) -> None:
    probe = (
        f"{sys.executable} -c "
        "\"import os; print(os.path.samestat(os.fstat(0), os.stat(os.devnull)))\""
    )
    with _stdin_is_a_pipe():
        exit_code, output, _ = _run_command_with_timeout(probe, tmp_path, 30)
    assert exit_code == 0, output
    assert output.strip() == "True", output


def test_child_cannot_make_the_server_stdin_non_blocking(tmp_path: Path) -> None:
    flip = (
        f"{sys.executable} -c "
        "\"import fcntl, os; "
        "fcntl.fcntl(0, fcntl.F_SETFL, fcntl.fcntl(0, fcntl.F_GETFL) | os.O_NONBLOCK)\""
    )
    with _stdin_is_a_pipe():
        exit_code, output, _ = _run_command_with_timeout(flip, tmp_path, 30)
        flags = fcntl.fcntl(0, fcntl.F_GETFL)
    assert exit_code == 0, output
    assert not flags & os.O_NONBLOCK
