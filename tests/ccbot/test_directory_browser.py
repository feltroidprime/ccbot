"""Tests for directory browser UI builders."""
from unittest.mock import MagicMock, patch

from ccbot.handlers.directory_browser import (
    build_machine_picker,
    build_permissions_picker,
)


def test_build_machine_picker_has_cancel_button():
    mock_reg = MagicMock()
    mock_reg.all.return_value = [
        MagicMock(machine_id="local"),
        MagicMock(machine_id="fedora"),
    ]
    mock_reg.display_name.side_effect = lambda x: x.capitalize()
    with patch("ccbot.handlers.directory_browser.machine_registry", mock_reg):
        text, keyboard = build_machine_picker()
    assert "Select Machine" in text
    flat_buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert any("Cancel" in b.text for b in flat_buttons)
    assert any("local".capitalize() in b.text or "Local" in b.text for b in flat_buttons)


def test_build_permissions_picker_has_both_options():
    mock_reg = MagicMock()
    mock_reg.display_name.return_value = "Fedora"
    with patch("ccbot.handlers.directory_browser.machine_registry", mock_reg):
        text, keyboard = build_permissions_picker("fedora", "/home/user/projects/foo")
    assert "foo" in text
    assert "Fedora" in text
    flat_buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert any("Normal" in b.text for b in flat_buttons)
    assert any("⚡" in b.text for b in flat_buttons)
