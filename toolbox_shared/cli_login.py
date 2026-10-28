"""Carry login failures across Python CLI boundaries without parsing tracebacks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys

ERROR_PREFIX = "TOOLBOX_ERROR="


def emit_login_error(exc) -> None:
    from .qingneng_login import LoginError
    if isinstance(exc, LoginError) and os.environ.get("TOOLBOX_CLI_PROTOCOL") == "1":
        print(ERROR_PREFIX + json.dumps(dict(code="network" if exc.kind in {"network", "server"} else "authentication",
              stage="login", login_kind=exc.kind, exception_type="LoginError", message=str(exc)), ensure_ascii=False), flush=True)


def script_command(command):
    command = list(command)
    if command and (Path(command[0]).name.startswith("python") or Path(command[0]).resolve() == Path(sys.executable).resolve()):
        index = 1
        while index < len(command) and command[index] in {"-u", "-B"}:
            index += 1
        if index < len(command) and Path(command[index]).suffix in {".py", ".pyw"}:
            if Path(command[index]).resolve() != Path(__file__).resolve():
                return [*command[:index], str(Path(__file__).resolve()), *command[index:]]
    return command


if __name__ == "__main__":
    # Running a file rather than -m also works for tools launched from their own cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from toolbox_shared.cli_login import emit_login_error
    script = Path(sys.argv[1]).resolve()
    os.environ["TOOLBOX_CLI_PROTOCOL"] = "1"
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name="__main__")
    except Exception as exc:
        emit_login_error(exc)
        raise
