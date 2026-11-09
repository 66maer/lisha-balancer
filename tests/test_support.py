"""Environment reports remain atomic during simultaneous application starts."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from lisha_overlay import support


def test_simultaneous_environment_reports_use_independent_temporary_files(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "collect_environment", lambda: {"writer": threading.get_ident()})
    replace = support.os.replace
    ready = threading.Barrier(2)
    replacement_lock = threading.Lock()
    temporary_files = []

    def replace_together(source, target):
        temporary_files.append(Path(source))
        ready.wait(timeout=5)
        # Windows may briefly lock the destination during replacement.
        # Both writers must retain their own complete source until this step.
        with replacement_lock:
            replace(source, target)

    monkeypatch.setattr(support.os, "replace", replace_together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(support.record_environment, [tmp_path, tmp_path]))
    assert len(set(temporary_files)) == 2
    assert json.loads((tmp_path / "environment.json").read_text(encoding="utf8")) in reports
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_environment_replace_keeps_previous_report_and_removes_temporary(tmp_path, monkeypatch):
    target = tmp_path / "environment.json"
    target.write_text('{"previous": true}', encoding="utf8")
    monkeypatch.setattr(support, "collect_environment", lambda: {"new": True})

    def fail_replace(source, destination):
        raise PermissionError("busy")

    monkeypatch.setattr(support.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        support.record_environment(tmp_path)
    assert json.loads(target.read_text(encoding="utf8")) == {"previous": True}
    assert not list(tmp_path.glob("*.tmp"))
