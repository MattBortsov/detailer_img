from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError

from car_wrap.bot.main import set_bot_commands_best_effort


@pytest.mark.asyncio
async def test_command_menu_failure_does_not_block_polling_startup() -> None:
    bot = AsyncMock()
    bot.set_my_commands.side_effect = TelegramNetworkError(
        method=AsyncMock(),
        message="timeout",
    )

    await set_bot_commands_best_effort(bot)

    bot.set_my_commands.assert_awaited_once()
