"""Tests for forward_command_handler — command forwarding to Claude Code."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(text: str, user_id: int = 1, thread_id: int = 42) -> MagicMock:
    """Build a minimal mock Update with message text in a forum topic."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = 100
    return update


def _make_context() -> MagicMock:
    """Build a minimal mock context."""
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


class TestForwardCommand:
    @pytest.mark.asyncio
    async def test_model_sends_command_to_tmux(self):
        """/model → send_to_window called with "/model"."""
        update = _make_update("/model")
        context = _make_context()

        mock_machine = AsyncMock()
        mock_machine.find_window_by_id = AsyncMock(return_value=MagicMock())

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.machine_registry") as mock_registry,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_registry.get.return_value = mock_machine
            mock_sm.get_binding_for_thread.return_value = MagicMock(
                window_id="@5", machine="local"
            )
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with(
                "@5", "/model", machine_id="local"
            )

    @pytest.mark.asyncio
    async def test_cost_sends_command_to_tmux(self):
        """/cost → send_to_window called with "/cost"."""
        update = _make_update("/cost")
        context = _make_context()

        mock_machine = AsyncMock()
        mock_machine.find_window_by_id = AsyncMock(return_value=MagicMock())

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.machine_registry") as mock_registry,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_registry.get.return_value = mock_machine
            mock_sm.get_binding_for_thread.return_value = MagicMock(
                window_id="@5", machine="local"
            )
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with(
                "@5", "/cost", machine_id="local"
            )

    @pytest.mark.asyncio
    async def test_clear_clears_session(self):
        """/clear → send_to_window + clear_window_session."""
        update = _make_update("/clear")
        context = _make_context()

        mock_machine = AsyncMock()
        mock_machine.find_window_by_id = AsyncMock(return_value=MagicMock())

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.machine_registry") as mock_registry,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_registry.get.return_value = mock_machine
            mock_sm.get_binding_for_thread.return_value = MagicMock(
                window_id="@5", machine="local"
            )
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with(
                "@5", "/clear", machine_id="local"
            )
            mock_sm.clear_window_session.assert_called_once_with("@5")
