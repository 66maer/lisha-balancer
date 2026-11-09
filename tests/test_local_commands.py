import pytest
from lisha_overlay.app import _requested_command


def test_local_analysis_and_restore_commands():
    assert _requested_command(["main.py"]) == "restore"
    for mode in ("all", "fan", "circle"):
        assert _requested_command(["main.py", "--analyze", mode]) == "analyze:" + mode
    assert _requested_command(["main.py", "--quit", "--analyze", "all"]) == "quit"
    assert _requested_command(["main.py", "--locate"]) == "locate"
    assert _requested_command(["main.py", "--tank", "g1p1"]) == "tank:g1p1"


def test_invalid_mode_cannot_be_submitted_to_running_instance():
    for args in (["main.py", "--analyze"], ["main.py", "--analyze", "unknown"]):
        with pytest.raises(ValueError):
            _requested_command(args)


@pytest.mark.parametrize("value", [None, "g0p1", "g1p6", "g9p1", "Alice"])
def test_invalid_tank_command_is_rejected(value):
    with pytest.raises(ValueError):
        _requested_command(["main.py", "--tank"] + ([] if value is None else [value]))
