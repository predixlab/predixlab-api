from unittest.mock import patch

import pytest

import start


@pytest.mark.parametrize("debug,reload", [(None, False), ("False", False), ("True", True)])
def test_debug_is_opt_in(monkeypatch, debug, reload):
    monkeypatch.delenv("DEBUG", raising=False)
    if debug is not None:
        monkeypatch.setenv("DEBUG", debug)
    monkeypatch.setenv("WORKERS", "2")
    with patch.object(start.uvicorn, "run") as run:
        start.main()
    assert run.call_args.kwargs["reload"] is reload
    assert run.call_args.kwargs["log_level"] == ("debug" if reload else "info")
    assert run.call_args.kwargs.get("workers") == (None if reload else 2)
