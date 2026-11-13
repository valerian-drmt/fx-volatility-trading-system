"""Supervised background loops (#337, audit 06#10): a crash is logged,
counted and restarted with backoff; a clean return ends the supervision;
cancellation passes through; a one-shot's exception reaches the log."""
from __future__ import annotations

import asyncio
import logging

import pytest

from shared import tasks
from shared.tasks import log_task_result, run_supervised, spawn_supervised


@pytest.mark.asyncio
async def test_crash_is_logged_counted_and_restarted(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(tasks, "RESTART_BACKOFF_S", (0.0, 0.0))
    counted: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks, "_count_restart", lambda e, t: counted.append((e, t)))
    runs = {"n": 0}

    async def loop() -> None:
        runs["n"] += 1
        if runs["n"] < 3:
            raise RuntimeError(f"crash {runs['n']}")

    with caplog.at_level(logging.ERROR):
        await run_supervised(loop, name="demo_loop", engine="execution_engine", backoff_s=(0.0, 0.0))
    assert runs["n"] == 3, "two crashes, two restarts, then a clean return ends it"
    assert counted == [("execution_engine", "demo_loop")] * 2
    crashed = [r for r in caplog.records if r.getMessage() == "engine_task_crashed"]
    assert len(crashed) == 2 and crashed[0].task == "demo_loop" and crashed[1].attempt == 2


@pytest.mark.asyncio
async def test_no_restart_mode_stops_after_the_first_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "_count_restart", lambda e, t: None)
    runs = {"n": 0}

    async def loop() -> None:
        runs["n"] += 1
        raise RuntimeError("once")

    await run_supervised(loop, name="x", engine="e", restart=False, backoff_s=(0.0,))
    assert runs["n"] == 1


@pytest.mark.asyncio
async def test_cancellation_passes_through_and_backoff_caps() -> None:
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = spawn_supervised(forever, name="forever", engine="e")
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tasks.RESTART_BACKOFF_S[-1] == 30.0


@pytest.mark.asyncio
async def test_one_shot_exception_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    async def boom() -> None:
        raise ValueError("fill handler died")

    task = asyncio.create_task(boom(), name="fill_cb")
    task.add_done_callback(lambda t: log_task_result(t, engine="execution_engine"))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)
    failed = [r for r in caplog.records if r.getMessage() == "background_task_failed"]
    assert failed and failed[0].task == "fill_cb" and "fill handler died" in str(failed[0].exc_info[1])


def test_execution_engine_loops_are_spawned_supervised() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "src" / "engines" / "execution" / "main.py").read_text(encoding="utf-8")
    assert src.count("spawn_supervised(") == 9, "the nine side loops"
    assert "asyncio.create_task(" not in src.split("app = FastAPI(")[0].split("async def lifespan")[1]
