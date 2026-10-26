"""Read a credential directly or from an interactive shell, never print/store it."""

from __future__ import annotations
import os
import subprocess
import sys
from .config import Credential
from .io import TechnicalFailure


def read_credential(config: Credential) -> str:
    if config.mode == "env":
        value = os.environ.get(config.env_name)
    else:
        # The shell's stdout may contain arbitrary profile text. A private pipe
        # is used on POSIX; the token is never mixed into shell diagnostic output.
        if os.name == "posix" and config.shell_kind == "posix":
            import shlex

            rfd, wfd = os.pipe()
            script = (
                "import os;v=os.environ.get("
                + repr(config.env_name)
                + ", '');os.write("
                + str(wfd)
                + ",v.encode())"
            )
            command = (
                "exec " + shlex.quote(sys.executable) + " -c " + shlex.quote(script)
            )
            try:
                result = subprocess.run(
                    [config.shell_executable, "-ic", command],
                    pass_fds=(wfd,),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=config.shell_timeout,
                )
                os.close(wfd)
                wfd = -1
                value = os.read(rfd, 65536).decode() if result.returncode == 0 else ""
            except subprocess.TimeoutExpired:
                raise TechnicalFailure(
                    "interactive credential shell timed out"
                ) from None
            finally:
                os.close(rfd)
                if wfd != -1:
                    os.close(wfd)
        elif config.shell_kind == "powershell":
            # Profile execution is intentional; only the framed final value is
            # parsed in memory. Child stdout/stderr must never be logged.
            marker = "DECOMPCOND_CREDENTIAL_"
            script = (
                "[Console]::Write('"
                + marker
                + "' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$env:"
                + config.env_name
                + ")))"
            )
            try:
                result = subprocess.run(
                    [config.shell_executable, "-NoLogo", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=config.shell_timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                import base64

                value = (
                    base64.b64decode(result.stdout.rsplit(marker, 1)[-1]).decode()
                    if marker in result.stdout
                    else ""
                )
            except (subprocess.TimeoutExpired, ValueError):
                raise TechnicalFailure("interactive credential shell failed") from None
        else:
            raise TechnicalFailure(
                "POSIX shell credential mode requires a POSIX host; select powershell or env"
            )
    if not value or not value.strip():
        raise TechnicalFailure(
            f"credential variable {config.env_name} is missing in {config.mode} mode"
        )
    return value.strip()
