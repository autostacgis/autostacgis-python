from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from jsonschema.exceptions import ValidationError

from autostacgis.triggers import FileTrigger, TimedTrigger


def test_trigger_registration_is_inert_and_required_parameters_are_checked_on_start():
    from autostacgis import Application

    application = Application()
    assert "watchdog" in application.capabilities["trigger"]
    with pytest.raises(ValidationError, match="watches"):
        application.trigger("watchdog")


def app(tmp_path):
    return SimpleNamespace(
        catalog={"version": "1.0.0", "plugins": []},
        base_dir=tmp_path,
        path=lambda value: tmp_path / value,
        request=lambda value: value or {"goal": {"output": {}}},
    )


def test_timed_date_job_passes_raw_arguments_and_closes_cleanly(tmp_path, monkeypatch):
    pytest.importorskip("apscheduler")
    called = Event()
    invocations = []

    def execute(catalog, base_dir, request):
        invocations.append((catalog, base_dir, request))
        called.set()

    monkeypatch.setattr("autostacgis.triggers.timed.execute", execute)
    controller = TimedTrigger(
        app(tmp_path),
        {
            "scheduler": {"timezone": "UTC"},
            "trigger": "date",
            "trigger_args": {"run_date": datetime.now(timezone.utc) + timedelta(milliseconds=100)},
            "job": {"id": "one-shot", "misfire_grace_time": 5},
        },
    )
    with controller:
        assert called.wait(3)
    assert len(invocations) == 1
    assert not controller.scheduler.running


def test_file_trigger_filters_and_debounces_real_files_and_preserves_move_destination(tmp_path, monkeypatch):
    pytest.importorskip("watchdog")
    from watchdog.events import FileMovedEvent

    called = Event()
    requests = []

    def execute(catalog, base_dir, request):
        requests.append(request)
        called.set()

    monkeypatch.setattr("autostacgis.triggers.files.execute", execute)
    controller = FileTrigger(
        app(tmp_path),
        {
            "watches": [{"path": ".", "recursive": True}],
            "handler": {"patterns": ["*.tif"], "ignore_directories": True},
            "events": ["created", "modified", "moved"],
            "debounce_seconds": 0.1,
        },
    )
    with controller:
        (tmp_path / "ignored.txt").write_text("no")
        (tmp_path / "image.tif").write_text("test")
        assert called.wait(4)
        assert all(value["query"]["trigger_event"]["src_path"].endswith("image.tif") for value in requests)
        called.clear()
        controller.handler.dispatch(
            FileMovedEvent(str(tmp_path / "temporary.part"), str(tmp_path / "final.tif"))
        )
        assert called.wait(4)
    assert requests[-1]["query"]["trigger_event"]["dest_path"] == str(tmp_path / "final.tif")
    assert requests[-1]["query"]["files"] == [str(tmp_path / "final.tif")]
    assert controller.last_error is None
    assert not controller.observer.is_alive()
    assert not controller.worker.is_alive()


def test_file_trigger_keeps_running_after_a_failed_workflow(tmp_path, monkeypatch):
    pytest.importorskip("watchdog")
    from watchdog.events import FileCreatedEvent

    done = Event()

    def execute(*args):
        done.set()
        raise ValueError("bad workflow")

    monkeypatch.setattr("autostacgis.triggers.files.execute", execute)
    controller = FileTrigger(app(tmp_path), {"watches": [{"path": "."}], "debounce_seconds": 0})
    with controller:
        controller.enqueue(FileCreatedEvent(str(Path(tmp_path) / "data.tif")))
        assert done.wait(3)
    assert isinstance(controller.last_error, ValueError)


def test_file_discovery_limits_runs_to_the_changed_file(tmp_path):
    from autostacgis.local_discovery import LocalFiles

    for name in ("one.tif", "two.tif"):
        (tmp_path / name).touch()
    context = app(tmp_path)
    context.stac = SimpleNamespace(extensions=lambda item: None)
    records = list(
        LocalFiles().discover({"search_glob": "*.tif"}, {"files": [str(tmp_path / "two.tif")]}, context)
    )
    assert len(records) == 1
    assert records[0].runtime["product_path"] == str(tmp_path / "two.tif")
