"""Handler for the /newgame command to start a new game cycle."""

from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from ..services import LMSClient
from ..services.lms_api import LMSAPIError

logger = logging.getLogger(__name__)


async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /newgame command to start a new game cycle.

    This command:
    1. Checks if a new game can be started (winner exists, fixtures available)
    2. Shows information about the next cycle and matchday
    3. Asks for confirmation via inline keyboard buttons
    4. Starts the new game on confirmation
    """
    message = update.message
    if not message:
        return

    lms = _get_lms_client(context)
    if not lms:
        await message.reply_text("Bot misconfigured: LMS client missing.")
        return

    try:
        # Check if a new game can be started
        check_result = await lms.check_new_game()

        if not check_result.get("can_start"):
            error_msg = check_result.get("error", "Cannot start a new game at this time.")
            await message.reply_text(f"❌ {error_msg}")
            return

        # Extract information
        next_cycle = check_result.get("next_cycle")
        next_matchday = check_result.get("next_matchday")
        fixtures_available = check_result.get("fixtures_available", 0)
        remaining_matchdays = check_result.get("remaining_matchdays", 0)
        warning = check_result.get("warning")

        # Build confirmation message
        confirmation_text = f"🎮 Ready to start a new game!\n\n"
        confirmation_text += f"📊 Next cycle: {next_cycle}\n"
        confirmation_text += f"⚽ Starting matchday: {next_matchday}\n"
        confirmation_text += f"📅 Fixtures available: {fixtures_available}\n"
        confirmation_text += f"📈 Remaining matchdays: {remaining_matchdays}\n"

        if warning:
            confirmation_text += f"\n⚠️ {warning}\n"

        confirmation_text += f"\n✅ All previous players will be automatically included.\n"
        confirmation_text += f"All player statuses will be reset to 'active'.\n\n"
        confirmation_text += f"Do you want to start the new game?"

        # Create inline keyboard for confirmation
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Start Game", callback_data=f"newgame_confirm_{next_cycle}"),
                InlineKeyboardButton("❌ Cancel", callback_data="newgame_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await message.reply_text(confirmation_text, reply_markup=reply_markup)

    except LMSAPIError as e:
        logger.error(f"LMS API error in newgame_command: {e}")
        await message.reply_text(f"❌ Error: {e}")
    except Exception as e:
        logger.exception("Unexpected error in newgame_command")
        await message.reply_text(f"❌ An unexpected error occurred: {e}")


async def newgame_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from the /newgame confirmation buttons."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    lms = _get_lms_client(context)
    if not lms:
        await query.edit_message_text("Bot misconfigured: LMS client missing.")
        return

    try:
        # Parse callback data
        data = query.data

        if data == "newgame_cancel":
            await query.edit_message_text("❌ New game creation cancelled.")
            return

        if data and data.startswith("newgame_confirm_"):
            # Extract cycle number from callback data (for verification)
            cycle_str = data.replace("newgame_confirm_", "")

            # Show processing message
            await query.edit_message_text("🔄 Starting new game...")

            # Call the API to start the new game
            result = await lms.start_new_game()

            if result.get("success"):
                cycle_number = result.get("cycle_number")
                round_id = result.get("round_id")
                pl_matchday = result.get("pl_matchday")
                fixtures_added = result.get("fixtures_added", 0)

                success_msg = f"✅ New game started successfully!\n\n"
                success_msg += f"📊 Cycle: {cycle_number}\n"
                success_msg += f"🎯 Round: 1\n"
                success_msg += f"⚽ Matchday: {pl_matchday}\n"
                success_msg += f"📅 Fixtures loaded: {fixtures_added}\n\n"
                success_msg += f"All players have been reset to 'active' status.\n"
                success_msg += f"Announcements have been sent to all players."

                await query.edit_message_text(success_msg)
            else:
                error_msg = result.get("error", "Failed to start new game")
                await query.edit_message_text(f"❌ Error: {error_msg}")

    except LMSAPIError as e:
        logger.error(f"LMS API error in newgame_callback: {e}")
        await query.edit_message_text(f"❌ Error: {e}")
    except Exception as e:
        logger.exception("Unexpected error in newgame_callback")
        await query.edit_message_text(f"❌ An unexpected error occurred: {e}")


def build_handlers() -> list:
    """Handlers needed for new game management."""
    return [
        CommandHandler("newgame", newgame_command),
        CallbackQueryHandler(newgame_callback, pattern="^newgame_"),
    ]


def _get_lms_client(context: ContextTypes.DEFAULT_TYPE) -> LMSClient | None:
    obj = context.application.bot_data.get("lms_client") if context.application else None
    return obj if isinstance(obj, LMSClient) else None
