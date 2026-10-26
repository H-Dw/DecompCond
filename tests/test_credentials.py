import os
import pytest
from decompcond.credentials import read_credential
from decompcond.io import TechnicalFailure


def test_env_mode_never_falls_back(config, monkeypatch):
    c = config.agent.credentials.model_copy(
        update={"mode": "env", "env_name": "DECOMPCOND_TEST_KEY"}
    )
    monkeypatch.delenv(c.env_name, raising=False)
    with pytest.raises(TechnicalFailure):
        read_credential(c)
    monkeypatch.setenv(c.env_name, "test-only-token")
    assert read_credential(c) == "test-only-token"


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell test")
def test_interactive_shell_secret_pipe_ignores_profile_stdout(
    config, monkeypatch, tmp_path
):
    shell = tmp_path / "shell"
    shell.write_text(
        '#!/bin/sh\necho noisy-profile\nexport DECOMPCOND_TEST_KEY=test-only-token\nexec /bin/sh -c "$2"\n'
    )
    shell.chmod(0o700)
    c = config.agent.credentials.model_copy(
        update={
            "mode": "interactive_shell",
            "env_name": "DECOMPCOND_TEST_KEY",
            "shell_executable": str(shell),
            "shell_kind": "posix",
        }
    )
    assert read_credential(c) == "test-only-token"
