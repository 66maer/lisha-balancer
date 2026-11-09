from lisha_overlay import app as application


def test_occupied_primary_hotkey_uses_visible_fallback(monkeypatch):
    calls = []

    def register(key, modifiers, virtual_key):
        calls.append((key, modifiers, virtual_key))
        return key != application.HotkeyFilter.LOCK_ID or bool(modifiers & application.MOD_SHIFT)

    monkeypatch.setattr(application, "register_hotkey", register)
    filter = application.HotkeyFilter(lambda: None, lambda: None)
    assert filter.registered[filter.LOCK_ID]
    assert filter.labels[filter.LOCK_ID] == "Ctrl+Alt+Shift+L"
    assert filter.labels[filter.HIDE_ID] == "Ctrl+Alt+H"
    assert len(calls) == 3
