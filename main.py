import asyncio
import html
import logging
import math
import os
import random
import secrets
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nexus-mafia")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit(
        "BOT_TOKEN is missing. Add it in Replit under Tools > Secrets, then restart the bot."
    )

ADMIN_ID_TEXT = os.getenv("ADMIN_ID", "").strip()
try:
    ADMIN_ID = int(ADMIN_ID_TEXT) if ADMIN_ID_TEXT else None
except ValueError:
    logger.error("ADMIN_ID must be a numeric Telegram user ID; /give will be disabled.")
    ADMIN_ID = None

DATABASE_PATH = Path(__file__).resolve().with_name("nexus_mafia.db")
REGISTRATION_SECONDS = 45
DISCUSSION_SECONDS = 30
VOTING_SECONDS = 25
NIGHT_SECONDS = 30
MIN_PLAYERS = 3
WINNER_MONEY = 1500
WINNER_DIAMONDS = 10
WIN_XP = 50
PARTICIPATION_XP = 20
XP_PER_LEVEL = 100
BOT_ADD_GROUP_URL = "https://t.me/NesuxmafiaBot?startgroup=true"
BOT_PRIVATE_URL = BOT_ADD_GROUP_URL.split("?", 1)[0]
NIGHT_ANIMATION_PATH = (
    Path(__file__).resolve().parent
    / "attached_assets"
    / "generated_images"
    / "mafia_night.gif"
)
NEWS_CHANNEL_URL = "https://t.me/xotira_vibe"
GAME_GROUPS_URL = "https://t.me/+KLE09zyH-o0yN2Fi"
DAILY_BONUS_MONEY = 100
DAILY_BONUS_DIAMONDS = 1

ROLE_MAFIA = "Mafiya"
ROLE_DOCTOR = "Shifokor"
ROLE_COMMISSIONER = "Komissar"
ROLE_CITIZEN = "Tinch aholi"

ROLE_CODES = {
    "m": ("mafia", ROLE_MAFIA),
    "d": ("doctor", ROLE_DOCTOR),
    "c": ("cop", ROLE_COMMISSIONER),
}

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


@dataclass(slots=True)
class Player:
    user_id: int
    name: str
    role: str = ROLE_CITIZEN
    alive: bool = True


@dataclass
class Game:
    chat_id: int
    chat_title: str = ""
    session_id: str = field(default_factory=lambda: secrets.token_hex(4))
    players: dict[int, Player] = field(default_factory=dict)
    phase: str = "registration"
    registration_message_id: int | None = None
    registration_deadline: float | None = None
    round_no: int = 0
    settings: dict[str, bool] = field(default_factory=dict)
    votes: dict[int, int] = field(default_factory=dict)
    actions: dict[str, int | None] = field(
        default_factory=lambda: {"mafia": None, "doctor": None, "cop": None}
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


games: dict[int, Game] = {}
background_tasks: set[asyncio.Task] = set()


def init_db() -> None:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                money INTEGER NOT NULL DEFAULT 0,
                diamonds INTEGER NOT NULL DEFAULT 0,
                display_name TEXT NOT NULL DEFAULT '',
                xp INTEGER NOT NULL DEFAULT 0,
                total_games INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                is_pro INTEGER NOT NULL DEFAULT 0,
                pro_since TEXT,
                rewarded_level INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        user_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        migrations = {
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "xp": "INTEGER NOT NULL DEFAULT 0",
            "total_games": "INTEGER NOT NULL DEFAULT 0",
            "wins": "INTEGER NOT NULL DEFAULT 0",
            "is_pro": "INTEGER NOT NULL DEFAULT 0",
            "pro_since": "TEXT",
            "rewarded_level": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in migrations.items():
            if column not in user_columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                commissioner_enabled INTEGER NOT NULL DEFAULT 1,
                doctor_self_heal INTEGER NOT NULL DEFAULT 1,
                random_tie_break INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS couples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                games_together INTEGER NOT NULL DEFAULT 0,
                wins_together INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_couples_active_user1
            ON couples(user1_id) WHERE status = 'active'
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_couples_active_user2
            ON couples(user2_id) WHERE status = 'active'
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS couple_requests (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bonus_claims (
                user_id INTEGER PRIMARY KEY,
                claim_date TEXT NOT NULL,
                money INTEGER NOT NULL,
                diamonds INTEGER NOT NULL,
                claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

# /extend buyrug'i - vaqtni 3 daqiqaga uzaytirish
@dp.message(Command("extend"))
async def extend_time_handler(message: types.Message, bot: Bot):
    extend_text = (
        "⏳ **Ro'yxatga olish vaqti 3 daqiqaga uzaytirildi!**\n\n"
        "O'yinga qo'shilishga ulgurmagan do'stlaringizni taklif qiling! 🎲"
    )
    await message.reply(extend_text, parse_mode=ParseMode.MARKDOWN)

def get_balance(user_id: int) -> tuple[int, int]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        row = conn.execute(
            "SELECT money, diamonds FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def add_reward(user_id: int, money: int, diamonds: int) -> tuple[int, int]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO users (user_id, money, diamonds)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    money = users.money + excluded.money,
                    diamonds = users.diamonds + excluded.diamonds
                """,
                (user_id, money, diamonds),
            )
            row = conn.execute(
                "SELECT money, diamonds FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
    return int(row[0]), int(row[1])


def claim_daily_bonus(user_id: int, display_name: str) -> tuple[bool, int, int]:
    today_utc = datetime.now(timezone.utc).date().isoformat()
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,),
            )
            conn.execute(
                "UPDATE users SET display_name = ? WHERE user_id = ?",
                (display_name, user_id),
            )
            previous_claim = conn.execute(
                "SELECT claim_date FROM daily_bonus_claims WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if previous_claim and previous_claim[0] == today_utc:
                balance = conn.execute(
                    "SELECT money, diamonds FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                conn.commit()
                return False, int(balance[0]), int(balance[1])

            conn.execute(
                """
                UPDATE users
                SET money = money + ?, diamonds = diamonds + ?
                WHERE user_id = ?
                """,
                (DAILY_BONUS_MONEY, DAILY_BONUS_DIAMONDS, user_id),
            )
            conn.execute(
                """
                INSERT INTO daily_bonus_claims (user_id, claim_date, money, diamonds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    claim_date = excluded.claim_date,
                    money = excluded.money,
                    diamonds = excluded.diamonds,
                    claimed_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    today_utc,
                    DAILY_BONUS_MONEY,
                    DAILY_BONUS_DIAMONDS,
                ),
            )
            balance = conn.execute(
                "SELECT money, diamonds FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            conn.commit()
            return True, int(balance[0]), int(balance[1])
        except Exception:
            conn.rollback()
            raise


def level_for_xp(xp: int) -> int:
    return max(1, xp // XP_PER_LEVEL + 1)


def level_reward(level: int) -> tuple[int, int]:
    fixed_rewards = {
        2: (500, 0),
        3: (750, 3),
        4: (1000, 5),
        5: (1500, 10),
    }
    return fixed_rewards.get(level, (500 + level * 100, 2 + level // 2))


def get_user_profile(user_id: int, display_name: str = "") -> dict:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,),
            )
            if display_name:
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE user_id = ?",
                    (display_name, user_id),
                )
            row = conn.execute(
                """
                SELECT user_id, money, diamonds, display_name, xp,
                       total_games, wins, is_pro, pro_since, rewarded_level
                FROM users WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
    return {
        "user_id": int(row[0]),
        "money": int(row[1]),
        "diamonds": int(row[2]),
        "display_name": row[3],
        "xp": int(row[4]),
        "total_games": int(row[5]),
        "wins": int(row[6]),
        "is_pro": bool(row[7]),
        "pro_since": row[8],
        "rewarded_level": int(row[9]),
        "level": level_for_xp(int(row[4])),
    }


def record_game_result(user_id: int, display_name: str, won: bool) -> list[tuple[int, int, int]]:
    xp_gain = WIN_XP if won else PARTICIPATION_XP
    winner_money = WINNER_MONEY if won else 0
    winner_diamonds = WINNER_DIAMONDS if won else 0
    received_level_rewards: list[tuple[int, int, int]] = []

    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,),
            )
            conn.execute(
                "UPDATE users SET display_name = ? WHERE user_id = ?",
                (display_name, user_id),
            )
            row = conn.execute(
                "SELECT xp, rewarded_level FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            new_xp = int(row[0]) + xp_gain
            new_level = level_for_xp(new_xp)
            first_unrewarded_level = max(1, int(row[1])) + 1
            level_money = 0
            level_diamonds = 0
            for level in range(first_unrewarded_level, new_level + 1):
                money, diamonds = level_reward(level)
                level_money += money
                level_diamonds += diamonds
                received_level_rewards.append((level, money, diamonds))

            conn.execute(
                """
                UPDATE users
                SET xp = ?,
                    total_games = total_games + 1,
                    wins = wins + ?,
                    money = money + ?,
                    diamonds = diamonds + ?,
                    rewarded_level = MAX(rewarded_level, ?)
                WHERE user_id = ?
                """,
                (
                    new_xp,
                    int(won),
                    winner_money + level_money,
                    winner_diamonds + level_diamonds,
                    new_level,
                    user_id,
                ),
            )
    return received_level_rewards


def set_pro_status(user_id: int, enabled: bool, display_name: str = "") -> None:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,),
            )
            if display_name:
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE user_id = ?",
                    (display_name, user_id),
                )
            conn.execute(
                """
                UPDATE users
                SET is_pro = ?, pro_since = CASE
                    WHEN ? = 1 THEN COALESCE(pro_since, CURRENT_TIMESTAMP)
                    ELSE NULL
                END
                WHERE user_id = ?
                """,
                (int(enabled), int(enabled), user_id),
            )


DEFAULT_GROUP_SETTINGS = {
    "commissioner_enabled": True,
    "doctor_self_heal": True,
    "random_tie_break": False,
}
GROUP_SETTING_LABELS = {
    "commissioner_enabled": "Komissar roli",
    "doctor_self_heal": "Shifokor o'zini davolashi",
    "random_tie_break": "Teng ovozda tasodifiy chiqarish",
}


def get_group_settings(chat_id: int) -> dict[str, bool]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO group_settings (chat_id) VALUES (?)",
                (chat_id,),
            )
            row = conn.execute(
                """
                SELECT commissioner_enabled, doctor_self_heal, random_tie_break
                FROM group_settings WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
    return {
        "commissioner_enabled": bool(row[0]),
        "doctor_self_heal": bool(row[1]),
        "random_tie_break": bool(row[2]),
    }


def toggle_group_setting(chat_id: int, setting: str) -> dict[str, bool]:
    columns = set(DEFAULT_GROUP_SETTINGS)
    if setting not in columns:
        raise ValueError("Unknown group setting")
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO group_settings (chat_id) VALUES (?)",
                (chat_id,),
            )
            conn.execute(
                f"UPDATE group_settings SET {setting} = 1 - {setting} WHERE chat_id = ?",
                (chat_id,),
            )
    return get_group_settings(chat_id)


def request_or_match_couple(user_id: int, display_name: str) -> tuple[str, dict | None]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, display_name) VALUES (?, ?)",
                (user_id, display_name),
            )
            conn.execute(
                "UPDATE users SET display_name = ? WHERE user_id = ?",
                (display_name, user_id),
            )
            active = conn.execute(
                """
                SELECT id FROM couples
                WHERE status = 'active' AND (user1_id = ? OR user2_id = ?)
                """,
                (user_id, user_id),
            ).fetchone()
            if active:
                conn.commit()
                return "already_paired", None

            conn.execute(
                """
                INSERT OR IGNORE INTO couple_requests (user_id, display_name)
                VALUES (?, ?)
                """,
                (user_id, display_name),
            )
            candidates = conn.execute(
                """
                SELECT user_id, display_name
                FROM couple_requests
                WHERE user_id != ?
                ORDER BY requested_at, user_id
                """,
                (user_id,),
            ).fetchall()

            for candidate_id, candidate_name in candidates:
                candidate_id = int(candidate_id)
                candidate_active = conn.execute(
                    """
                    SELECT id FROM couples
                    WHERE status = 'active' AND (user1_id = ? OR user2_id = ?)
                    """,
                    (candidate_id, candidate_id),
                ).fetchone()
                if candidate_active:
                    conn.execute(
                        "DELETE FROM couple_requests WHERE user_id = ?",
                        (candidate_id,),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO couples (user1_id, user2_id, status)
                    VALUES (?, ?, 'active')
                    """,
                    (user_id, candidate_id),
                )
                conn.execute(
                    "DELETE FROM couple_requests WHERE user_id IN (?, ?)",
                    (user_id, candidate_id),
                )
                conn.commit()
                return "matched", {
                    "user_id": candidate_id,
                    "display_name": candidate_name or f"ID {candidate_id}",
                }

            conn.commit()
            return "waiting", None
        except Exception:
            conn.rollback()
            raise


def get_active_couple(user_id: int) -> dict | None:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        row = conn.execute(
            """
            SELECT id, user1_id, user2_id, status, games_together, wins_together,
                   created_at
            FROM couples
            WHERE status = 'active' AND (user1_id = ? OR user2_id = ?)
            """,
            (user_id, user_id),
        ).fetchone()
        if row is None:
            return None
        partner_id = int(row[2] if int(row[1]) == user_id else row[1])
        partner = conn.execute(
            "SELECT display_name, is_pro FROM users WHERE user_id = ?",
            (partner_id,),
        ).fetchone()
    return {
        "id": int(row[0]),
        "partner_id": partner_id,
        "partner_name": (partner[0] if partner else "") or f"ID {partner_id}",
        "partner_is_pro": bool(partner[1]) if partner else False,
        "status": row[3],
        "games_together": int(row[4]),
        "wins_together": int(row[5]),
        "created_at": row[6],
    }


def end_couple(user_id: int, couple_id: int | None = None) -> str:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            query = """
                SELECT id, user1_id, user2_id FROM couples
                WHERE status = 'active' AND (user1_id = ? OR user2_id = ?)
            """
            params: tuple = (user_id, user_id)
            if couple_id is not None:
                query += " AND id = ?"
                params += (couple_id,)
            row = conn.execute(query, params).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE couples SET status = 'ended', ended_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (row[0],),
                )
                conn.execute(
                    "DELETE FROM couple_requests WHERE user_id IN (?, ?)",
                    (row[1], row[2]),
                )
                return "ended"
            removed = conn.execute(
                "DELETE FROM couple_requests WHERE user_id = ?",
                (user_id,),
            ).rowcount
    return "request_cancelled" if removed else "not_found"


def update_couple_game_stats(
    participant_ids: set[int],
    winner_ids: set[int],
) -> None:
    if len(participant_ids) < 2:
        return
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        with conn:
            active_pairs = conn.execute(
                """
                SELECT id, user1_id, user2_id FROM couples
                WHERE status = 'active'
                """
            ).fetchall()
            for couple_id, user1_id, user2_id in active_pairs:
                first, second = int(user1_id), int(user2_id)
                if first in participant_ids and second in participant_ids:
                    both_won = int(first in winner_ids and second in winner_ids)
                    conn.execute(
                        """
                        UPDATE couples
                        SET games_together = games_together + 1,
                            wins_together = wins_together + ?
                        WHERE id = ? AND status = 'active'
                        """,
                        (both_won, couple_id),
                    )


def is_current_game(game: Game) -> bool:
    return games.get(game.chat_id) is game


def remove_game(game: Game) -> None:
    game.phase = "finished"
    if is_current_game(game):
        games.pop(game.chat_id, None)


async def send_abort_message(game: Game, text: str) -> None:
    try:
        await bot.send_message(game.chat_id, text)
    except TelegramAPIError:
        logger.exception("Could not send an error message to chat %s", game.chat_id)


def spawn_background(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    background_tasks.add(task)

    def on_done(finished: asyncio.Task) -> None:
        background_tasks.discard(finished)
        if finished.cancelled():
            return
        error = finished.exception()
        if error:
            logger.error(
                "A game task ended unexpectedly",
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(on_done)


def make_keyboard(buttons: list[InlineKeyboardButton], width: int = 2) -> InlineKeyboardMarkup:
    rows = [buttons[index : index + width] for index in range(0, len(buttons), width)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def registration_keyboard(game: Game) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎲 O'yinga qo'shilish",
                    callback_data=f"join:{game.session_id}:{game.chat_id}",
                )
            ]
        ]
    )


def game_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔮 O'yin boshlandi!",
                    url=BOT_PRIVATE_URL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Sizning rolingiz",
                    url=BOT_PRIVATE_URL,
                )
            ],
        ]
    )


def night_phase_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤖 Botga o'tish",
                    url=BOT_PRIVATE_URL,
                )
            ]
        ]
    )


def action_keyboard(game: Game, actor: Player, role_code: str) -> InlineKeyboardMarkup:
    buttons = []
    for player in game.players.values():
        if not player.alive:
            continue
        if role_code in {"m", "c"} and player.user_id == actor.user_id:
            continue
        if (
            role_code == "d"
            and not game.settings.get("doctor_self_heal", True)
            and player.user_id == actor.user_id
        ):
            continue
        buttons.append(
            InlineKeyboardButton(
                text=player.name[:48],
                callback_data=(
                    f"act:{game.session_id}:{game.chat_id}:"
                    f"{game.round_no}:{role_code}:{player.user_id}"
                ),
            )
        )
    return make_keyboard(buttons)


def vote_keyboard(game: Game) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=player.name[:48],
            callback_data=(
                f"vote:{game.session_id}:{game.chat_id}:"
                f"{game.round_no}:{player.user_id}"
            ),
        )
        for player in game.players.values()
        if player.alive
    ]
    return make_keyboard(buttons)


def escaped_name(player: Player) -> str:
    return html.escape(player.name)


def active_pro_user_ids(user_ids: list[int]) -> set[int]:
    if not user_ids:
        return set()
    placeholders = ",".join("?" for _ in user_ids)
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        return {
            int(row[0])
            for row in conn.execute(
                f"SELECT user_id FROM users WHERE is_pro = 1 AND user_id IN ({placeholders})",
                user_ids,
            ).fetchall()
        }


def registration_message_text(game: Game, seconds_remaining: int) -> str:
    minutes, seconds = divmod(max(0, seconds_remaining), 60)
    pro_ids = active_pro_user_ids(list(game.players))
    joined_players = ", ".join(
        f"{escaped_name(player)} | 👤"
        f"{' (PRO)' if player.user_id in pro_ids else ''}"
        for player in game.players.values()
    )
    return (
        "🎲 Ro'yxatga olish davom etmoqda\n"
        f"⏳ Vaqt: {minutes} daqiqa {seconds} soniya\n"
        "Ro'yxatdan o'tgan o'yinchilar:\n\n"
        f"{joined_players}\n\n"
        f"Jami: {len(game.players)}"
    )


def registration_seconds_remaining(game: Game) -> int:
    if game.registration_deadline is None:
        return REGISTRATION_SECONDS
    loop = asyncio.get_running_loop()
    return max(0, math.ceil(game.registration_deadline - loop.time()))


async def update_registration_message(
    game: Game,
    seconds_remaining: int,
) -> None:
    if game.registration_message_id is None:
        return
    async with game.lock:
        if not is_current_game(game) or game.phase != "registration":
            return
        try:
            await bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.registration_message_id,
                text=registration_message_text(game, seconds_remaining),
                reply_markup=registration_keyboard(game),
            )
        except TelegramAPIError:
            logger.warning(
                "Could not update registration message in chat %s",
                game.chat_id,
                exc_info=True,
            )


def settings_text(settings: dict[str, bool]) -> str:
    lines = ["⚙️ <b>Guruh sozlamalari</b>"]
    for setting, label in GROUP_SETTING_LABELS.items():
        state = "YOQILGAN" if settings.get(setting, False) else "O'CHIRILGAN"
        lines.append(f"• {html.escape(label)}: <b>{state}</b>")
    lines.append(
        "\nO'zgarishlar keyingi o'yindan boshlab qo'llanadi. "
        "Faqat guruh adminlari boshqara oladi."
    )
    return "\n".join(lines)


def settings_keyboard(chat_id: int, settings: dict[str, bool]) -> InlineKeyboardMarkup:
    rows = []
    for key, label in GROUP_SETTING_LABELS.items():
        state = "✅" if settings.get(key, False) else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{state} {label}",
                    callback_data=f"cfg:{chat_id}:{key}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def is_group_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        logger.exception("Could not verify group administrator %s in %s", user_id, chat_id)
        return False
    return member.status in {
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }


def couple_keyboard(couple_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Juftlikni ajratish",
                    callback_data=f"unpara:{couple_id}",
                )
            ]
        ]
    )


def couple_message(couple: dict) -> str:
    pro_badge = " 💠 <b>PRO</b>" if couple["partner_is_pro"] else ""
    status_label = "Faol" if couple["status"] == "active" else "Tugatilgan"
    return (
        "💞 <b>Sizning juftligingiz</b>\n"
        f"Juftingiz: <b>{html.escape(couple['partner_name'])}</b>{pro_badge}\n"
        f"Holat: <b>{status_label}</b>\n"
        f"Birga o'ynagan o'yinlar: <b>{couple['games_together']}</b>\n"
        f"Birgalikdagi g'alabalar: <b>{couple['wins_together']}</b>\n"
        f"Boshlangan vaqt (UTC): <code>{html.escape(couple['created_at'])}</code>"
    )


ROLE_SUMMARY_INFO = {
    ROLE_COMMISSIONER: ("town", "🕵️ Komissar Kattani"),
    ROLE_DOCTOR: ("town", "👨‍⚕️ Doktor"),
    ROLE_CITIZEN: ("town", "👤 Tinch aholi"),
    ROLE_MAFIA: ("mafia", "🔪 Mafiya"),
}
FACTION_SUMMARY_LABELS = {
    "town": "🕵️ Tinchlar",
    "mafia": "🔴 Mafiya",
    "neutral": "⚪ Neytrallar",
}


def alive_players_display(game: Game) -> str:
    alive_players = [player for player in game.players.values() if player.alive]
    pro_ids = active_pro_user_ids([player.user_id for player in alive_players])

    lines = [f"Tirik o'yinchilar ({len(alive_players)} kishi):"]
    faction_roles: dict[str, Counter[str]] = {
        "town": Counter(),
        "mafia": Counter(),
        "neutral": Counter(),
    }
    for number, player in enumerate(alive_players, 1):
        mention = (
            f'<a href="tg://user?id={player.user_id}">{html.escape(player.name)}</a>'
        )
        pro_badge = " (PRO)" if player.user_id in pro_ids else ""
        lines.append(f"{number}.{mention} | 👤{pro_badge}")

        faction, role_label = ROLE_SUMMARY_INFO.get(
            player.role,
            ("neutral", f"⚪ {html.escape(player.role)}"),
        )
        faction_roles[faction][role_label] += 1

    for faction in ("town", "mafia", "neutral"):
        role_counts = faction_roles[faction]
        total = sum(role_counts.values())
        if total == 0:
            continue
        roles_text = ", ".join(
            f"{role_label} — {count}"
            for role_label, count in role_counts.items()
        )
        lines.append(
            f"{FACTION_SUMMARY_LABELS[faction]} - {total}: {roles_text}"
        )
    return "\n".join(lines)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🪪 Shaxsiy kabinet",
                    callback_data="profile",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Botni guruhga qo'shish ➕",
                    url=BOT_ADD_GROUP_URL,
                ),
                InlineKeyboardButton(
                    text="📣 Yangiliklar ↗️",
                    url=NEWS_CHANNEL_URL,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎲 O'yin guruhlari",
                    url=GAME_GROUPS_URL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Kunlik bonus",
                    callback_data="daily_bonus",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Profilim",
                    callback_data="profile",
                ),
                InlineKeyboardButton(
                    text="📖 O'yin qoidalari",
                    callback_data="rules",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Top o'yinchilar",
                    callback_data="top",
                )
            ],
        ]
    )


def profile_text(user_id: int, display_name: str) -> str:
    profile = get_user_profile(user_id, display_name)
    badge = " 💠 <b>PRO</b>" if profile["is_pro"] else ""
    return (
        f"👤 <b>{html.escape(display_name)}</b>{badge}\n\n"
        f"⭐ Daraja: <b>{profile['level']}</b>\n"
        f"💰 Pul: <b>{profile['money']}</b>\n"
        f"💎 Olmos: <b>{profile['diamonds']}</b>\n"
        f"✨ Jami XP: <b>{profile['xp']}</b>\n"
        f"🎮 Jami o'yinlar: <b>{profile['total_games']}</b>\n"
        f"🏆 G'alabalar: <b>{profile['wins']}</b>"
    )


async def send_menu_callback_message(call: types.CallbackQuery, text: str) -> None:
    await call.answer()
    if call.message is not None and isinstance(call.message, types.Message):
        await call.message.answer(text)
    else:
        await bot.send_message(call.from_user.id, text)


@dp.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    if message.from_user is not None:
        get_user_profile(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "Salom!\nMen 🎩 Nexus Mafia o'yini rasmiy botiman.",
        reply_markup=main_menu_keyboard(),
    )


@dp.callback_query(F.data == "profile")
async def cb_menu_profile(call: types.CallbackQuery) -> None:
    await send_menu_callback_message(
        call,
        profile_text(call.from_user.id, call.from_user.full_name),
    )


@dp.callback_query(F.data == "daily_bonus")
async def cb_daily_bonus(call: types.CallbackQuery) -> None:
    claimed, money, diamonds = claim_daily_bonus(
        call.from_user.id,
        call.from_user.full_name,
    )
    if claimed:
        text = (
            "🎁 <b>Kunlik bonus olindi!</b>\n"
            f"+{DAILY_BONUS_MONEY} 💰 va +{DAILY_BONUS_DIAMONDS} 💎 hisobingizga qo'shildi.\n"
            f"Yangi balans: {money} 💰 | {diamonds} 💎"
        )
    else:
        text = (
            "🎁 Bugungi kunlik bonusni allaqachon oldingiz.\n"
            "Yangi bonus UTC vaqti bilan yarim tunda ochiladi."
        )
    await send_menu_callback_message(call, text)


@dp.callback_query(F.data == "rules")
async def cb_menu_rules(call: types.CallbackQuery) -> None:
    await send_menu_callback_message(
        call,
        "📖 <b>Nexus Mafia o'yin qoidalari</b>\n\n"
        "1. Guruhda <code>/start_mafia</code> yuborib, ro'yxatdan o'tish tugmasini bosing. "
        f"O'yin uchun kamida {MIN_PLAYERS} o'yinchi kerak.\n"
        "2. O'yin boshlanganda rolingiz shaxsiy xabarda yuboriladi. Buning uchun botga "
        "avval shaxsiy xabarda <code>/start</code> yuboring.\n"
        "3. Kechasi Mafiya nishon tanlaydi, Shifokor o'yinchini davolaydi, "
        "Komissar esa mafiyaligini tekshiradi.\n"
        "4. Kunduzi o'yinchilar muhokama qilib, ovoz bilan bir kishini chiqaradi.\n"
        "5. Barcha Mafiya chiqarilsa, tinch aholi yutadi. Mafiya tirik o'yinchilarning "
        "yarmiga yetib yoki undan oshib ketsa, Mafiya yutadi.\n\n"
        "Har bir guruh admini <code>/settings</code> orqali ayrim qoidalarni o'zgartirishi mumkin.",
    )


@dp.callback_query(F.data == "top")
async def cb_menu_top(call: types.CallbackQuery) -> None:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        rows = conn.execute(
            """
            SELECT display_name, user_id, wins, total_games, xp, is_pro
            FROM users
            WHERE total_games > 0
            ORDER BY wins DESC, xp DESC, total_games DESC, user_id ASC
            LIMIT 10
            """
        ).fetchall()
    if not rows:
        await send_menu_callback_message(
            call,
            "🏆 Hozircha reyting uchun o'yin natijalari yo'q.",
        )
        return

    lines = ["🏆 <b>Top o'yinchilar</b>", ""]
    for rank, (name, user_id, wins, games_played, xp, is_pro) in enumerate(rows, 1):
        safe_name = html.escape(name or f"Foydalanuvchi {user_id}")
        badge = " 💠" if is_pro else ""
        lines.append(
            f"{rank}. <b>{safe_name}</b>{badge} — "
            f"{wins} g'alaba / {games_played} o'yin | {xp} XP"
        )
    await send_menu_callback_message(call, "\n".join(lines))


@dp.message(Command("balans"))
async def cmd_balance(message: types.Message) -> None:
    if message.from_user is None:
        return
    profile = get_user_profile(message.from_user.id, message.from_user.full_name)
    await message.answer(
        f"👤 <b>{html.escape(message.from_user.full_name)} balans:</b>\n\n"
        f"💰 Pul: <b>{profile['money']}</b>\n💎 Olmos: <b>{profile['diamonds']}</b>"
    )


@dp.message(Command("give"))
async def cmd_give(message: types.Message) -> None:
    if message.from_user is None:
        return
    if ADMIN_ID is None:
        await message.answer(
            "Bu buyruq o'chirilgan. Uni yoqish uchun Replit Secrets bo'limida "
            "<code>ADMIN_ID</code> ni Telegram raqamli ID ingizga o'rnating."
        )
        return
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Bu buyruq faqat administrator uchun.")
        return

    parts = (message.text or "").split()
    if len(parts) != 4:
        await message.answer(
            "Ishlatish: <code>/give user_id pul olmos</code>"
        )
        return

    try:
        target_id, money, diamonds = map(int, parts[1:])
    except ValueError:
        await message.answer("ID va mukofotlar butun son bo'lishi kerak.")
        return

    if target_id <= 0 or money < 0 or diamonds < 0:
        await message.answer("ID musbat, mukofotlar esa manfiy bo'lmasligi kerak.")
        return

    new_money, new_diamonds = add_reward(target_id, money, diamonds)
    await message.answer(
        f"✅ <code>{target_id}</code> foydalanuvchisiga "
        f"<b>+{money} 💰</b> va <b>+{diamonds} 💎</b> berildi.\n"
        f"Yangi balans: {new_money} pul | {new_diamonds} olmos"
    )


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message) -> None:
    if message.from_user is None:
        return
    profile = get_user_profile(message.from_user.id, message.from_user.full_name)
    badge = " 💠 <b>PRO</b>" if profile["is_pro"] else ""
    await message.answer(
        f"👤 <b>{html.escape(message.from_user.full_name)}</b>{badge}\n\n"
        f"⭐ Daraja: <b>{profile['level']}</b>\n"
        f"💰 Pul: <b>{profile['money']}</b>\n"
        f"💎 Olmos: <b>{profile['diamonds']}</b>\n"
        f"✨ Jami XP: <b>{profile['xp']}</b>\n"
        f"🎮 Jami o'yinlar: <b>{profile['total_games']}</b>\n"
        f"🏆 G'alabalar: <b>{profile['wins']}</b>"
    )


@dp.message(Command("pro"))
async def cmd_pro(message: types.Message) -> None:
    if message.from_user is None:
        return
    profile = get_user_profile(message.from_user.id, message.from_user.full_name)
    if profile["is_pro"]:
        await message.answer(
            "💠 <b>PRO holati faol</b>\n"
            "Maxsus PRO nishoni profilingizda ko'rinadi.\n"
            f"Faollashtirilgan: <code>{html.escape(profile['pro_since'] or 'noma’lum')}</code>"
        )
        return

    admin_note = (
        "Xarid kelishilgach, bot administratori "
        f"<code>/givepro {message.from_user.id}</code> buyrug'i bilan PRO'ni yoqadi."
        if ADMIN_ID is not None
        else "Hozircha PRO'ni faollashtiradigan administrator sozlanmagan."
    )
    await message.answer(
        "💠 <b>Nexus PRO</b>\n\n"
        "PRO a'zoligi maxsus 💠 nishonni profilingizda va juftlik sahifalarida ko'rsatadi. "
        "PRO o'yin natijasi yoki imkoniyatlariga ta'sir qilmaydi.\n\n"
        f"{admin_note}\n"
        "To'lov bot ichida qayta ishlanmaydi; xarid shartlarini administrator bilan kelishib oling."
    )


@dp.message(Command("gifts"))
async def cmd_gifts(message: types.Message) -> None:
    if message.from_user is None:
        return
    profile = get_user_profile(message.from_user.id, message.from_user.full_name)
    level = profile["level"]
    progress = profile["xp"] % XP_PER_LEVEL
    remaining = XP_PER_LEVEL - progress
    upcoming = []
    for reward_level in range(level + 1, level + 4):
        money, diamonds = level_reward(reward_level)
        upcoming.append(
            f"• <b>{reward_level}-daraja</b>: {money} 💰 | {diamonds} 💎"
        )
    next_money, next_diamonds = level_reward(level + 1)
    await message.answer(
        f"🎁 <b>Daraja sovg'alari</b>\n"
        f"Joriy daraja: <b>{level}</b>\n"
        f"Jami XP: <b>{profile['xp']}</b>\n"
        f"Joriy darajadagi XP: <b>{progress}/{XP_PER_LEVEL}</b>\n"
        f"Keyingi sovg'agacha: <b>{remaining} XP</b>\n\n"
        f"Keyingi daraja sovg'asi: <b>{next_money} 💰 | {next_diamonds} 💎</b>\n"
        "Darajaga yetganda sovg'a avtomatik beriladi.\n\n"
        + "\n".join(upcoming)
    )

def get_roles_text() -> str:
    lines = ["🎭 O'yin rollari:\n"]
    if isinstance(ROLES, dict):
        for key, value in ROLES.items():
            if isinstance(value, dict):
                role_name = value.get("name", key)
                role_desc = value.get("description", "Tavsif yo'q")
            else:
                role_name = key
                role_desc = str(value)
            lines.append(f"• {role_name} — {role_desc}")
    elif isinstance(ROLES, list):
        for item in ROLES:
            lines.append(f"• {item}")
    return "\n".join(lines)

@dp.message(Command("my_role"))
async def cmd_my_role(message: types.Message) -> None:
    if message.from_user is None:
        return

    if message.chat.type in {"group", "supergroup"}:
        candidates = [games.get(message.chat.id)]
    else:
        candidates = list(games.values())
    registered = [
        game
        for game in candidates
        if game is not None
        and game.phase == "registration"
        and message.from_user.id in game.players
    ]
    if registered:
        await message.answer(
            "Siz o'yinga ro'yxatdan o'tgansiz. O'yin boshlanganda rolingiz shaxsiy xabarda yuboriladi."
        )
        return
    active = [
        game
        for game in candidates
        if game is not None
        and message.from_user.id in game.players
        and game.phase not in {"registration", "finished"}
    ]
    if not active:
        await message.answer("Hozir faol o'yinda qatnashmayapsiz.")
        return

    lines = ["🎭 <b>Sizning faol o'yinlardagi rolingiz:</b>"]
    for game in active:
        player = game.players[message.from_user.id]
        if player.role == ROLE_CITIZEN and game.phase == "starting":
            role_text = "Rol yuborilmoqda..."
        else:
            role_text = html.escape(player.role)
        life = "tirik" if player.alive else "o'yindan chiqqan"
        group_name = html.escape(game.chat_title or str(game.chat_id))
        lines.append(
            f"• <b>{group_name}</b>: {role_text} — {life}; bosqich: {html.escape(game.phase)}"
        )
    await message.answer("\n".join(lines))

def get_roles_text() -> str:
    lines = ["🎭 O'yin rollari (Jami 40 ta):\n"]
    for key, data in ROLES.items():
        name = data.get("name", key)
        desc = data.get("description", "")
        lines.append(f"• {name} — {desc}")
    return "\n".join(lines)

@dp.message(Command("roles"))
async def cmd_roles(message: types.Message):
    try:
        full_text = get_roles_text()
        # Matn juda uzun bo'lsa, Telegram chekloviga ko'ra bo'lib yuboramiz
        if len(full_text) > 4000:
            for i in range(0, len(full_text), 4000):
                await message.answer(full_text[i:i+4000])
        else:
            await message.answer(full_text)
    except Exception as e:
        await message.answer(f"Xatolik yuz berdi: {e}")





@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(
        "📖 <b>Nexus Mafia — buyruqlar qo'llanmasi</b>\n\n"
        "<b>O'yin:</b>\n"
        "• <code>/start_mafia</code> — guruhda ro'yxatdan o'tishni boshlash\n"
        "• <code>/my_role</code> — faol o'yindagi rolingiz\n"
        "• <code>/roles</code> — rollar va vazifalar\n"
        "• <code>/settings</code> — guruh adminlari uchun o'yin sozlamalari\n\n"
        "<b>Profil va sovg'alar:</b>\n"
        "• <code>/profile</code> — balans, daraja, o'yinlar va g'alabalar\n"
        "• <code>/balans</code> — pul va olmos balansi\n"
        "• <code>/gifts</code> — XP va keyingi daraja sovg'alari\n"
        "• <code>/pro</code> — PRO holati va faollashtirish ma'lumoti\n\n"
        "<b>Juftlik:</b>\n"
        "• <code>/para</code> yoki <code>/parafind</code> — juft izlash/navbatga turish\n"
        "• <code>/mypara</code> — juftlik va birgalikdagi natijalar\n"
        "• <code>/unpara</code> — juftlikni ajratish yoki navbatdan chiqish\n\n"
        "<b>Boshqa:</b>\n"
        "• <code>/start</code> — boshlash va bot bilan tanishish\n"
        "• <code>/give user_id pul olmos</code> — admin mukofoti\n"
        "• <code>/givepro user_id</code> / <code>/removepro user_id</code> — admin PRO boshqaruvi"
    )


async def apply_pro_command(message: types.Message, enabled: bool) -> None:
    if message.from_user is None:
        return
    if ADMIN_ID is None:
        await message.answer(
            "Admin buyruqlari o'chirilgan. Replit Secrets bo'limida "
            "<code>ADMIN_ID</code> ni sozlang."
        )
        return
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Bu buyruq faqat administrator uchun.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        command = "givepro" if enabled else "removepro"
        await message.answer(f"Ishlatish: <code>/{command} user_id</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("Foydalanuvchi ID si butun son bo'lishi kerak.")
        return
    if target_id <= 0:
        await message.answer("ID musbat son bo'lishi kerak.")
        return

    set_pro_status(target_id, enabled)
    state = "faollashtirildi" if enabled else "o'chirildi"
    await message.answer(
        f"💠 <code>{target_id}</code> foydalanuvchisining PRO holati {state}."
    )
    try:
        await bot.send_message(
            target_id,
            "💠 Sizning PRO holatingiz faollashtirildi!"
            if enabled
            else "PRO holatingiz administrator tomonidan o'chirildi.",
        )
    except TelegramAPIError:
        logger.info("Could not send PRO status notification to user %s", target_id)


@dp.message(Command("givepro"))
async def cmd_givepro(message: types.Message) -> None:
    await apply_pro_command(message, enabled=True)


@dp.message(Command("removepro"))
async def cmd_removepro(message: types.Message) -> None:
    await apply_pro_command(message, enabled=False)


@dp.message(Command("settings"))
async def cmd_settings(message: types.Message) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Guruh sozlamalarini guruh ichida oching.")
        return
    if message.from_user is None or not await is_group_admin(
        message.chat.id, message.from_user.id
    ):
        await message.answer(
            "⚠️ Sozlamalarni faqat guruh adminlari o'zgartirishi mumkin."
        )
        return
    settings = get_group_settings(message.chat.id)
    await message.answer(
        settings_text(settings),
        reply_markup=settings_keyboard(message.chat.id, settings),
    )


@dp.callback_query(F.data.startswith("cfg:"))
async def cb_group_setting(call: types.CallbackQuery) -> None:
    if call.message is None or not isinstance(call.message, types.Message):
        await call.answer("Bu tugma endi ishlamaydi.", show_alert=True)
        return
    try:
        _, chat_id_text, setting = (call.data or "").split(":")
        chat_id = int(chat_id_text)
    except (ValueError, AttributeError):
        await call.answer("Noto'g'ri sozlama tugmasi.", show_alert=True)
        return
    if (
        call.message.chat.id != chat_id
        or call.message.chat.type not in {"group", "supergroup"}
        or setting not in GROUP_SETTING_LABELS
    ):
        await call.answer("Bu sozlama tugmasi yaroqsiz.", show_alert=True)
        return
    if not await is_group_admin(chat_id, call.from_user.id):
        await call.answer(
            "Faqat guruh adminlari sozlamalarni o'zgartira oladi.",
            show_alert=True,
        )
        return
    settings = toggle_group_setting(chat_id, setting)
    try:
        await call.message.edit_text(
            settings_text(settings),
            reply_markup=settings_keyboard(chat_id, settings),
        )
    except TelegramAPIError:
        logger.exception("Could not refresh settings message for chat %s", chat_id)
    await call.answer("Sozlama saqlandi. Keyingi o'yindan boshlab qo'llanadi.")


@dp.message(Command("para"))
@dp.message(Command("parafind"))
async def cmd_find_couple(message: types.Message) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    display_name = message.from_user.full_name
    get_user_profile(user_id, display_name)
    current = get_active_couple(user_id)
    if current:
        await message.answer(
            couple_message(current),
            reply_markup=couple_keyboard(current["id"]),
        )
        return

    status, partner = request_or_match_couple(user_id, display_name)
    if status == "already_paired":
        current = get_active_couple(user_id)
        if current:
            await message.answer(
                couple_message(current),
                reply_markup=couple_keyboard(current["id"]),
            )
        return
    if status == "waiting":
        await message.answer(
            "💞 Juft izlash navbatiga qo'shildingiz. Boshqa o'yinchi ham "
            "<code>/para</code> yoki <code>/parafind</code> yuborganda juftlik yaratiladi."
        )
        return

    current = get_active_couple(user_id)
    if current:
        await message.answer(
            "💞 Juft topildi!\n" + couple_message(current),
            reply_markup=couple_keyboard(current["id"]),
        )
    if partner:
        try:
            await bot.send_message(
                partner["user_id"],
                "💞 Sizga juft topildi! Juftligingizni ko'rish uchun <code>/mypara</code> yuboring.",
            )
        except TelegramAPIError:
            logger.info("Could not notify matched user %s", partner["user_id"])


@dp.message(Command("mypara"))
async def cmd_my_couple(message: types.Message) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    get_user_profile(user_id, message.from_user.full_name)
    couple = get_active_couple(user_id)
    if couple:
        await message.answer(
            couple_message(couple),
            reply_markup=couple_keyboard(couple["id"]),
        )
        return
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as conn:
        waiting = conn.execute(
            "SELECT 1 FROM couple_requests WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if waiting:
        await message.answer(
            "💞 Hozir juft izlash navbatidasiz. Kutishni bekor qilish uchun "
            "<code>/unpara</code> yuboring."
        )
    else:
        await message.answer(
            "Sizda faol juftlik yo'q. Juft izlash uchun <code>/parafind</code> yuboring."
        )


@dp.message(Command("unpara"))
async def cmd_unpara(message: types.Message) -> None:
    if message.from_user is None:
        return
    current = get_active_couple(message.from_user.id)
    result = end_couple(message.from_user.id)
    if result == "ended":
        await message.answer("❌ Juftlik ajratildi. Juftingizga ham xabar yuborildi.")
        if current:
            try:
                await bot.send_message(
                    current["partner_id"],
                    "❌ Juftingiz juftlikni ajratdi.",
                )
            except TelegramAPIError:
                logger.info("Could not notify former partner %s", current["partner_id"])
    elif result == "request_cancelled":
        await message.answer("Juft izlash navbatidan chiqarildingiz.")
    else:
        await message.answer("Sizda faol juftlik yoki izlash so'rovi yo'q.")


@dp.callback_query(F.data.startswith("unpara:"))
async def cb_unpara(call: types.CallbackQuery) -> None:
    try:
        _, couple_id_text = (call.data or "").split(":")
        couple_id = int(couple_id_text)
    except (ValueError, AttributeError):
        await call.answer("Noto'g'ri tugma.", show_alert=True)
        return

    current = get_active_couple(call.from_user.id)
    if current is None or current["id"] != couple_id:
        await call.answer("Bu juftlik endi faol emas.", show_alert=True)
        return
    if end_couple(call.from_user.id, couple_id) != "ended":
        await call.answer("Juftlik allaqachon tugatilgan.", show_alert=True)
        return

    await call.answer("Juftlik ajratildi.")
    if call.message is not None and isinstance(call.message, types.Message):
        try:
            await call.message.edit_text("❌ Juftlik ajratildi.")
        except TelegramAPIError:
            logger.info("Could not update the couple message after separation")
    try:
        await bot.send_message(current["partner_id"], "❌ Juftingiz juftlikni ajratdi.")
    except TelegramAPIError:
        logger.info("Could not notify former partner %s", current["partner_id"])


@dp.message(Command("start_mafia"))
async def cmd_start_mafia(message: types.Message) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("O'yinni boshlash uchun meni guruhga qo'shing.")
        return

    if message.chat.id in games:
        await message.answer("⚠️ Bu guruhda o'yin allaqachon davom etmoqda.")
        return

    game = Game(
        chat_id=message.chat.id,
        chat_title=message.chat.title or "Guruh",
        settings=get_group_settings(message.chat.id),
    )
    game.registration_deadline = (
        asyncio.get_running_loop().time() + REGISTRATION_SECONDS
    )
    games[game.chat_id] = game

    try:
        registration_message = await message.answer(
            registration_message_text(game, REGISTRATION_SECONDS),
            reply_markup=registration_keyboard(game),
        )
        game.registration_message_id = registration_message.message_id
    except TelegramAPIError:
        remove_game(game)
        raise

    try:
        await bot.pin_chat_message(
            chat_id=game.chat_id,
            message_id=game.registration_message_id,
            disable_notification=True,
        )
    except TelegramAPIError as error:
        logger.warning(
            "Could not pin registration message in chat %s: %s",
            game.chat_id,
            error,
        )

    spawn_background(registration_countdown(game))


@dp.callback_query(F.data.startswith("join:"))
async def cb_join(call: types.CallbackQuery) -> None:
    if call.message is None or not isinstance(call.message, types.Message):
        await call.answer("Bu tugma endi ishlamaydi.", show_alert=True)
        return

    try:
        _, session_id, chat_id_text = (call.data or "").split(":")
        chat_id = int(chat_id_text)
    except (ValueError, AttributeError):
        await call.answer("Noto'g'ri tugma.", show_alert=True)
        return

    game = games.get(chat_id)
    if (
        game is None
        or game.session_id != session_id
        or call.message.chat.id != chat_id
    ):
        await call.answer("Bu o'yin tugagan.", show_alert=True)
        return

    async with game.lock:
        if game.phase != "registration":
            await call.answer("Ro'yxatdan o'tish yakunlangan.", show_alert=True)
            return
        if call.from_user.id in game.players:
            await call.answer("Siz allaqachon ro'yxatdan o'tgansiz.", show_alert=True)
            return

        try:
            await bot.send_message(
                call.from_user.id,
                "✅ Siz ro'yxatdan o'tdingiz. O'yin boshlanganda rolingiz shu yerga yuboriladi.",
            )
        except TelegramAPIError:
            await call.answer(
                "Avval botga shaxsiy xabarda /start yuboring, keyin qayta urinib ko'ring.",
                show_alert=True,
            )
            return

        if not is_current_game(game) or game.phase != "registration":
            await call.answer("Ro'yxatdan o'tish yakunlangan.", show_alert=True)
            return

        game.players[call.from_user.id] = Player(
            user_id=call.from_user.id,
            name=call.from_user.full_name,
        )
        get_user_profile(call.from_user.id, call.from_user.full_name)

    await call.answer("Siz o'yinga qo'shildingiz!")
    await update_registration_message(
        game,
        registration_seconds_remaining(game),
    )


async def registration_countdown(game: Game) -> None:
    try:
        while is_current_game(game) and game.phase == "registration":
            remaining = registration_seconds_remaining(game)
            if remaining <= 0:
                await update_registration_message(game, 0)
                break
            await asyncio.sleep(min(5, remaining))
            if not is_current_game(game) or game.phase != "registration":
                return
            await update_registration_message(
                game,
                registration_seconds_remaining(game),
            )

        if not is_current_game(game):
            return
        async with game.lock:
            if not is_current_game(game) or game.phase != "registration":
                return
            player_count = len(game.players)
            game.phase = "starting"

        if player_count < MIN_PLAYERS:
            await bot.send_message(
                game.chat_id,
                f"❌ O'yinchilar yetarli emas (kamida {MIN_PLAYERS} kerak). O'yin bekor qilindi.",
            )
            remove_game(game)
            return
        await start_game_process(game)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Game startup failed in chat %s", game.chat_id)
        await send_abort_message(
            game,
            "⚠️ O'yinni boshlashda xatolik yuz berdi. Qayta urinib ko'ring.",
        )
        remove_game(game)


async def start_game_process(game: Game) -> None:
    player_ids = list(game.players)
    roles = [ROLE_MAFIA, ROLE_DOCTOR]
    if len(player_ids) >= 4 and game.settings.get("commissioner_enabled", True):
        roles.append(ROLE_COMMISSIONER)
    roles.extend([ROLE_CITIZEN] * (len(player_ids) - len(roles)))
    random.shuffle(roles)

    for user_id, role in zip(player_ids, roles):
        game.players[user_id].role = role

    await bot.send_message(
        game.chat_id,
        "✅ Ro'yxatdan o'tish yakunlandi!\nRollar tarqatilmoqda...",
        reply_markup=game_start_keyboard(),
    )

    failed_dm_names: list[str] = []
    for player in game.players.values():
        try:
            await bot.send_message(
                player.user_id,
                f"🎭 Sizning rolingiz: <b>{html.escape(player.role)}</b>",
            )
        except TelegramAPIError:
            failed_dm_names.append(escaped_name(player))

    if failed_dm_names:
        await bot.send_message(
            game.chat_id,
            "❌ Ayrim o'yinchilarga shaxsiy xabar yuborilmadi, o'yin bekor qilindi.\n"
            + "\n".join(failed_dm_names)
            + "\nQayta o'yinga qo'shilishdan oldin har bir o'yinchi botga /start yuborsin.",
        )
        remove_game(game)
        return

    while is_current_game(game):
        await run_night_phase(game)
        if not is_current_game(game):
            return
        if await check_game_over(game):
            return

        await run_day_phase(game)
        if not is_current_game(game):
            return
        if await check_game_over(game):
            return


async def run_night_phase(game: Game) -> None:
    game.round_no += 1
    game.phase = "night"
    game.actions = {"mafia": None, "doctor": None, "cop": None}

    await bot.send_animation(
        game.chat_id,
        animation=FSInputFile(NIGHT_ANIMATION_PATH),
        caption=(
            f"🏙 Tun: {game.round_no}\n"
            "Ko'chaga faqat jasur va qo'rqmas odamlar chiqishdi. "
            "Ertalab tirik qolganlarni sanaymiz..."
        ),
        reply_markup=night_phase_keyboard(),
    )
    await bot.send_message(
        game.chat_id,
        f"{alive_players_display(game)}\n\n"
        f"⏳ Tonggacha {NIGHT_SECONDS} soniya qoldi.",
    )

    for player in list(game.players.values()):
        if not player.alive:
            continue
        role_code = next(
            (code for code, (_, role) in ROLE_CODES.items() if role == player.role),
            None,
        )
        if role_code is None:
            continue

        prompt = {
            "m": "🔪 Kimni nishonga olasiz?",
            "d": "🩺 Kimni davolaysiz?",
            "c": "🕵️‍♂️ Kimni tekshirasiz?",
        }[role_code]
        try:
            await bot.send_message(
                player.user_id,
                prompt,
                reply_markup=action_keyboard(game, player, role_code),
            )
        except TelegramAPIError:
            logger.warning(
                "Could not send a night action to player %s in chat %s",
                player.user_id,
                game.chat_id,
            )

    await asyncio.sleep(NIGHT_SECONDS)
    if not is_current_game(game) or game.phase != "night":
        return

    killed_id = game.actions["mafia"]
    saved_id = game.actions["doctor"]
    if killed_id is None:
        text = "Bu kecha mafiya hech kimni nishonga olmadi."
    elif killed_id == saved_id:
        text = "🕊 Shifokor o'z vaqtida yordam berdi — hech kim jabrlanmadi!"
    else:
        victim = game.players.get(killed_id)
        if victim is None or not victim.alive:
            text = "Bu kecha hech kim jabrlanmadi."
        else:
            victim.alive = False
            text = f"💀 Mafiya tunda <b>{escaped_name(victim)}</b> ni o'ldirdi."

    await bot.send_message(
        game.chat_id,
        "☀️ <b>KUN BOTDI, SHAHAR UYG'ONDI!</b>\n\n"
        + text
        + "\n\n"
        + alive_players_display(game),
    )


async def run_day_phase(game: Game) -> None:
    game.phase = "discussion"
    await bot.send_message(
        game.chat_id,
        "🗣 <b>MUHOKAMA BOSQICHI</b>\n"
        "Kim mafiya deb o'ylaysiz? Muhokama qiling.\n"
        f"{alive_players_display(game)}\n\n"
        f"⏳ Oqshomgacha {DISCUSSION_SECONDS} soniya qoldi.",
    )
    await asyncio.sleep(DISCUSSION_SECONDS)
    if not is_current_game(game):
        return

    game.phase = "voting"
    game.votes.clear()
    await bot.send_message(
        game.chat_id,
        "🗳 <b>OVOZ BERISH</b>\n"
        f"{alive_players_display(game)}\n\n"
        f"⏳ Oqshomgacha {VOTING_SECONDS} soniya qoldi.\n\n"
        "O'yindan chiqarish uchun bir kishini tanlang. O'z ovozingizni o'zgartirishingiz mumkin.",
        reply_markup=vote_keyboard(game),
    )
    await asyncio.sleep(VOTING_SECONDS)
    if not is_current_game(game) or game.phase != "voting":
        return

    if not game.votes:
        await bot.send_message(
            game.chat_id,
            "🤝 Hech kim ovoz bermadi, hech kim chiqarilmadi.\n\n"
            + alive_players_display(game),
        )
        return

    counts = Counter(game.votes.values())
    highest = max(counts.values())
    leaders = [target_id for target_id, count in counts.items() if count == highest]
    tie_text = ""
    if len(leaders) != 1:
        if game.settings.get("random_tie_break", False):
            expelled_id = random.choice(leaders)
            tie_text = "🤝 Ovozlar teng bo'ldi; tasodifiy tanlov qo'llandi.\n"
        else:
            await bot.send_message(
                game.chat_id,
                "🤝 Ovozlar teng bo'ldi. Hech kim o'yindan chiqarilmadi.\n\n"
                + alive_players_display(game),
            )
            return
    else:
        expelled_id = leaders[0]

    expelled = game.players.get(expelled_id)
    if expelled is None or not expelled.alive:
        await bot.send_message(game.chat_id, "Ovoz berish nishoni endi mavjud emas.")
        return

    expelled.alive = False
    await bot.send_message(
        game.chat_id,
        tie_text
        + f"🚫 Ovoz berish natijasida <b>{escaped_name(expelled)}</b> chiqarildi.\n"
        f"Uning roli: <b>{html.escape(expelled.role)}</b>\n\n"
        f"{alive_players_display(game)}",
    )


async def check_game_over(game: Game) -> bool:
    mafia_count = sum(
        player.alive and player.role == ROLE_MAFIA
        for player in game.players.values()
    )
    citizen_count = sum(
        player.alive and player.role != ROLE_MAFIA
        for player in game.players.values()
    )

    if mafia_count == 0:
        await finish_game(game, winner_side=ROLE_CITIZEN)
        return True
    if mafia_count >= citizen_count:
        await finish_game(game, winner_side=ROLE_MAFIA)
        return True
    return False


async def finish_game(game: Game, winner_side: str) -> None:
    if not is_current_game(game):
        return
    game.phase = "finished"
    lines = [
        f"🎉 <b>O'YIN YAKUNLANDI! G'OLIB: {html.escape(winner_side.upper())}</b>",
        "",
        "🏆 <b>Natijalar va mukofotlar:</b>",
    ]
    participant_ids = set(game.players)
    winner_ids: set[int] = set()

    for player in game.players.values():
        is_winner = (
            winner_side == ROLE_MAFIA and player.role == ROLE_MAFIA
        ) or (
            winner_side == ROLE_CITIZEN and player.role != ROLE_MAFIA
        )
        if is_winner:
            winner_ids.add(player.user_id)

        try:
            level_rewards = record_game_result(
                player.user_id,
                player.name,
                won=is_winner,
            )
            if is_winner:
                lines.append(
                    f"👤 {escaped_name(player)}: +{WINNER_MONEY} 💰 | "
                    f"+{WINNER_DIAMONDS} 💎 | +{WIN_XP} XP"
                )
            else:
                lines.append(
                    f"👤 {escaped_name(player)}: +{PARTICIPATION_XP} XP"
                )
            if level_rewards:
                for level, money, diamonds in level_rewards:
                    lines.append(
                        f"🎁 {escaped_name(player)} — {level}-daraja sovg'asi: "
                        f"+{money} 💰 | +{diamonds} 💎"
                    )
            try:
                personal_text = (
                    f"🏆 <b>G'olib bo'ldingiz!</b>\n+{WINNER_MONEY} 💰 va "
                    f"+{WINNER_DIAMONDS} 💎 olasiz.\n+{WIN_XP} XP."
                    if is_winner
                    else f"O'yin yakunlandi. Ishtirokingiz uchun +{PARTICIPATION_XP} XP."
                )
                if level_rewards:
                    personal_text += "\n\n🎁 <b>Daraja sovg'alari:</b>\n" + "\n".join(
                        f"{level}-daraja: +{money} 💰 | +{diamonds} 💎"
                        for level, money, diamonds in level_rewards
                    )
                await bot.send_message(player.user_id, personal_text)
            except TelegramAPIError:
                logger.warning("Could not notify player %s about game rewards", player.user_id)
        except sqlite3.Error:
            logger.exception("Could not save game stats for player %s", player.user_id)
            lines.append(f"👤 {escaped_name(player)}: natijalarni yozishda xatolik")

    try:
        update_couple_game_stats(participant_ids, winner_ids)
    except sqlite3.Error:
        logger.exception("Could not update couple game stats")

    try:
        await bot.send_message(game.chat_id, "\n".join(lines))
    except TelegramAPIError:
        logger.exception("Could not announce the winner in chat %s", game.chat_id)
    finally:
        remove_game(game)


@dp.callback_query(F.data.startswith("act:"))
async def cb_night_action(call: types.CallbackQuery) -> None:
    try:
        _, session_id, chat_id_text, round_text, role_code, target_text = (
            call.data or ""
        ).split(":")
        chat_id = int(chat_id_text)
        round_no = int(round_text)
        target_id = int(target_text)
    except (ValueError, AttributeError):
        await call.answer("Noto'g'ri yoki eskirgan tugma.", show_alert=True)
        return

    game = games.get(chat_id)
    if (
        game is None
        or game.session_id != session_id
        or game.round_no != round_no
        or game.phase != "night"
        or role_code not in ROLE_CODES
    ):
        await call.answer("Bu tungi yurish yakunlangan.", show_alert=True)
        return

    action_name, expected_role = ROLE_CODES[role_code]
    player = game.players.get(call.from_user.id)
    target = game.players.get(target_id)
    if player is None or not player.alive or player.role != expected_role:
        await call.answer("Sizda bu yurishni qilish huquqi yo'q.", show_alert=True)
        return
    if target is None or not target.alive:
        await call.answer("Bu o'yinchi endi faol emas.", show_alert=True)
        return
    if (
        role_code in {"m", "c"}
        or (role_code == "d" and not game.settings.get("doctor_self_heal", True))
    ) and target_id == player.user_id:
        await call.answer("O'zingizni tanlay olmaysiz.", show_alert=True)
        return
    if action_name == "cop" and game.actions["cop"] is not None:
        await call.answer("Bu kecha tekshiruvni allaqachon o'tkazdingiz.")
    game.actions[action_name] = target_id
    await call.answer(
        {
            "mafia": "Nishon tanlandi.",
            "doctor": "Bemor tanlandi.",
            "cop": "Tekshiruv natijasi yuborildi.",
        }[action_name]
    )

    if action_name == "cop":
        result = "Mafiya 🔴" if target.role == ROLE_MAFIA else "Tinch aholi 🟢"
        await bot.send_message(
            call.from_user.id,
            f"🕵️‍♂️ <b>Tekshiruv natijasi:</b> "
            f"{escaped_name(target)} — <b>{result}</b>",
        )


@dp.callback_query(F.data.startswith("vote:"))
async def cb_vote(call: types.CallbackQuery) -> None:
    if call.message is None or not isinstance(call.message, types.Message):
        await call.answer("Bu tugma endi ishlamaydi.", show_alert=True)
        return
    try:
        _, session_id, chat_id_text, round_text, target_text = (
            call.data or ""
        ).split(":")
        chat_id = int(chat_id_text)
        round_no = int(round_text)
        target_id = int(target_text)
    except (ValueError, AttributeError):
        await call.answer("Noto'g'ri yoki eskirgan tugma.", show_alert=True)
        return

    game = games.get(chat_id)
    if (
        game is None
        or game.session_id != session_id
        or game.round_no != round_no
        or game.phase != "voting"
        or call.message.chat.id != chat_id
    ):
        await call.answer("Ovoz berish yakunlangan.", show_alert=True)
        return

    voter = game.players.get(call.from_user.id)
    target = game.players.get(target_id)
    if voter is None or not voter.alive:
        await call.answer("Faqat tirik o'yinchilar ovoz bera oladi.", show_alert=True)
        return
    if target is None or not target.alive:
        await call.answer("Bu o'yinchi endi faol emas.", show_alert=True)
        return
    if target_id == voter.user_id:
        await call.answer("O'zingizga ovoz bera olmaysiz.", show_alert=True)
        return

    changed_vote = voter.user_id in game.votes
    game.votes[voter.user_id] = target_id
    await call.answer(
        "Ovozingiz yangilandi." if changed_vote else "Ovozingiz qabul qilindi."
    )


BOT_COMMANDS = [
    BotCommand(command="start", description="Botni ishga tushirish"),
    BotCommand(command="help", description="Buyruqlar qo'llanmasi"),
    BotCommand(command="start_mafia", description="Guruhda Mafia o'yinini boshlash"),
    BotCommand(command="my_role", description="Faol o'yindagi rolingiz"),
    BotCommand(command="roles", description="Mafia o'yini rollari"),
    BotCommand(command="settings", description="Guruh o'yin sozlamalari"),
    BotCommand(command="profile", description="Profil, statistika va daraja"),
    BotCommand(command="balans", description="Pul va olmos balansi"),
    BotCommand(command="gifts", description="XP va daraja sovg'alari"),
    BotCommand(command="pro", description="PRO holati va faollashtirish"),
    BotCommand(command="para", description="Juft izlash"),
    BotCommand(command="parafind", description="Juft izlash"),
    BotCommand(command="mypara", description="Juftlik va natijalar"),
    BotCommand(command="unpara", description="Juftlikni ajratish"),
    BotCommand(command="give", description="Admin: foydalanuvchiga mukofot berish"),
    BotCommand(command="givepro", description="Admin: PRO faollashtirish"),
    BotCommand(command="removepro", description="Admin: PRO o'chirish"),
]


async def register_bot_commands_until_success() -> None:
    retry_delay = 1
    while True:
        try:
            await bot.set_my_commands(BOT_COMMANDS, request_timeout=10)
        except TelegramAPIError as error:
            logger.warning(
                "Could not register Telegram commands; retrying in %s seconds: %s",
                retry_delay,
                error,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
        else:
            logger.info("Telegram bot buyruqlari ro'yxatdan o'tkazildi.")
async def main():
                bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                init_db()
                command_registration_task = asyncio.create_task(
                    register_bot_commands_until_success()
                )
                logger.info("Nexus Mafia bot ishga tushdi.")

                try:
                    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
                finally:
                    command_registration_task.cancel()
                    await asyncio.gather(command_registration_task, return_exceptions=True)
                    await bot.session.close()
async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    init_db()
    command_registration_task = asyncio.create_task(
        register_bot_commands_until_success()
    )
    logger.info("Nexus Mafia bot ishga tushdi.")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        command_registration_task.cancel()
        await asyncio.gather(command_registration_task, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        logger.exception("Bot stopped because of an unexpected error.")
        raise
@dp.message(Command("extend"))
async def extend_command_handler(message: Message):
    # Faqat guruh va superguruhlarda ishlashi uchun
    if message.chat.type not in ["group", "supergroup"]:
        return

    chat_id = message.chat.id
    game = games.get(chat_id)  # O'yin holatini olish

    # Agar o'yin bo'lmasa yoki registratsiya bosqichida bo'lmasa
    if not game or game.get("state") != "registration":
        await message.reply("⚠️ Hozirda registratsiya ketmayapti yoki o'yin boshlanmagan!")
        return

    # Registratsiya vaqtini 3 minutga (180 sekund) uzaytirish
    game["registration_end_time"] += 180
    await message.reply("⏳ Ro'yxatdan o'tish vaqti muvaffaqiyatli **3 minutga** uzaytirildi!")
@router.message(Command("extend"))
async def extend_command_handler(message: Message):
    chat_id = message.chat.id
    game = games.get(chat_id)

    # Agar o'yin bo'lmasa yoki registratsiya ketmayotgan bo'lsa
    if not game or game.get("state") != "registration":
        await message.reply("⚠️ Hozirda ro'yxatdan o'tish bosqichi ketmayapti!")
        return

    # Registratsiya vaqtini 3 minutga (180 soniya) uzaytirish
    game["registration_end_time"] += 180
    await message.reply("⏳ Ro'yxatdan o'tish vaqti muvaffaqiyatli **3 minutga** uzaytirildi!")
@dp.message(Command("extend"))
async def extend_command_handler(message: Message):
    chat_id = message.chat.id

    # Agar chatda o'yin bo'lmasa yoki games lug'atida topilmasa
    if chat_id not in games or not games[chat_id]:
        await message.reply("⚠️ Hozirda guruhda faol o'yin mavjud emas!")
        return

    game = games[chat_id]

    # Faqat registratsiya bosqichida vaqtni uzaytirishga ruxsat beriladi
    if game.get("state") != "registration":
        await message.reply("⚠️ Ro'yxatdan o'tish bosqichi tugagan! Vaqtni uzaytirib bo'lmaydi.")
        return

    # Registratsiya tugash vaqtini 3 minutga (180 soniya) uzaytirish
    if "registration_end_time" in game:
        game["registration_end_time"] += 180
    elif "timer" in game:
        game["timer"] += 180

    await message.reply("⏳ Ro'yxatdan o'tish vaqti **3 minutga** uzaytirildi!")
BotCommand(command="extend", description="Ro'yxatdan o'tish vaqtini 3 minutga uzaytirish"),
# Nexus Mafia botidagi rollar va ularning belgilari (stiker/emoji)
ROLES_CONFIG = {
    # Tinch fuqarolar va maxsus rollar
    "serjant": {
        "name": "Serjant",
        "icon": "👮🏼",
        "team": "citizens",
        "description": "Kechasi gumondorlarni tekshiradi yoki shaharni tartibda ushlaydi."
    },
    "donishmand": {
        "name": "Donishmand",
        "icon": "👨🏼‍🏫",
        "team": "citizens",
        "description": "O'yin va rollar haqida maslahat beruvchi va tahlil qiluvchi tinch fuqaro."
    },
    "ovchi": {
        "name": "Ovchi",
        "icon": "🏹",
        "team": "citizens",
        "description": "O'lsa, o'zi bilan birga boshqa bir o'yinchini ham olib ketadi."
    },
    "aka": {
        "name": "Aka",
        "icon": "👥",
        "team": "citizens",
        "description": "Ukasi kimligini biladi va u bilan birga harakat qiladi."
    },
    "uka": {
        "name": "Uka",
        "icon": "👥",
        "team": "citizens",
        "description": "Akasi kimligini biladi va birgalikda o'ynaydi."
    },
    "konchi": {
        "name": "Konchi",
        "icon": "👷🏻‍♂️",
        "team": "citizens",
        "description": "Mahsus qobiliyatga ega bo'lgan tinch fuqaro."
    },
    "voris": {
        "name": "Voris",
        "icon": "🧑🏻‍💼",
        "team": "citizens",
        "description": "Muayyan shartlar bajarilganda asosiy rolni egallashi mumkin."
    },

    # Mafiya jamoasi
    "don": {
        "name": "Don",
        "icon": "🤵🏻",
        "team": "mafia",
        "description": "Mafiyaning boshlig'i, kechasi qurbonni tanlaydi va Komissarni izlaydi."
    },
    "mafia": {
        "name": "Mafia",
        "icon": "🤵🏼",
        "team": "mafia",
        "description": "Don bilan birga kechasi tinch fuqarolarni yo'q qiladi."
    },

    # Mustaqil va xavfli rollar
    "qotil": {
        "name": "Qotil",
        "icon": "🔪",
        "team": "neutral",
        "description": "Yolg'iz o'ynaydi, har kecha xohlagan kishisini o'ldirishi mumkin."
    },
    "haqiqiy_vampir": {
        "name": "Haqiqiy vampir",
        "icon": "🧛🏻‍♂️",
        "team": "vampire",
        "description": "Vampirlar boshlig'i, kechasi o'yinchilarni tishlab o'z safiga qo'shadi."
    },
    "vampir": {
        "name": "Vampir",
        "icon": "🧛🏻",
        "team": "vampire",
        "description": "Haqiqiy vampirga yordam beradi va tuni bilan ov qiladi."
    }
}
ROLES_CONFIG = {
    # ==================== TINCH FUQAROLAR (18 TA) ====================
    "citizen": {
        "name": "Tinch fuqaro",
        "icon": "👨‍💼",
        "team": "citizens",
        "description": "Oddiy shahar tumani vaqili, ovoz berishda qatnashadi.",
    },
    "komissar": {
        "name": "Komissar",
        "icon": "🕵️‍♂️",
        "team": "citizens",
        "description": "Kechasi o'yinchining rolini yoki mafiya ekanligini tekshiradi.",
    },
    "serjant": {
        "name": "Serjant",
        "icon": "👮🏼",
        "team": "citizens",
        "description": "Komissarning yordamchisi. Komissar o'lsa, uning o'rniga o'tadi.",
    },
    "shifokor": {
        "name": "Shifokor",
        "icon": "👨‍⚕️",
        "team": "citizens",
        "description": "Kechasi bir kishini davolaydi va o'limdan saqlab qoladi.",
    },
    "donishmand": {
        "name": "Donishmand",
        "icon": "👨🏼‍🏫",
        "team": "citizens",
        "description": "O'yin holati va tahlillar bo'yicha maslahatchi.",
    },
    "ovchi": {
        "name": "Ovchi",
        "icon": "🏹",
        "team": "citizens",
        "description": "O'ldirilganda, o'zi bilan birga boshqa bir o'yinchini ham olib ketadi.",
    },
    "aka": {
        "name": "Aka",
        "icon": "👥",
        "team": "citizens",
        "description": "Ukasi kimligini biladi va u bilan birga harakat qiladi.",
    },
    "uka": {
        "name": "Uka",
        "icon": "👥",
        "team": "citizens",
        "description": "Akasi kimligini biladi va birga o'ynaydi.",
    },
    "konchi": {
        "name": "Konchi",
        "icon": "👷🏻‍♂️",
        "team": "citizens",
        "description": "Portlash va maxsus hujumlarga bardoshli tinch fuqaro.",
    },
    "voris": {
        "name": "Voris",
        "icon": "🧑🏻‍💼",
        "team": "citizens",
        "description": "Tinch fuqarolar yetakchisi o'lsa, uning vakolatini oladi.",
    },
    "advokat": {
        "name": "Advokat",
        "icon": "⚖️",
        "team": "citizens",
        "description": "Kechasi tanlagan kishisini kunduzgi sud (ovoz)dan himoya qiladi.",
    },
    "sochiq": {
        "name": "Sehrgar",
        "icon": "🧙‍♂️",
        "team": "citizens",
        "description": "Bir marta o'yinchini tiriltirishi yoki sehr ishlatishi mumkin.",
    },
    "qorovul": {
        "name": "Qorovul",
        "icon": "🔦",
        "team": "citizens",
        "description": "Kechasi kim kimning uyiga kirganini kuzatadi.",
    },
    "xaker": {
        "name": "Xaker",
        "icon": "💻",
        "team": "citizens",
        "description": "Kechasi biron o'yinchining guruhdagi ovozini muzlatib qo'yadi.",
    },
    "jurnalist": {
        "name": "Jurnalist",
        "icon": "📰",
        "team": "citizens",
        "description": "Ikki o'yinchining bir jamoada yoki yo'qligini solishtiradi.",
    },
    "mer": {
        "name": "Mer (Hokim)",
        "icon": "🏛️",
        "team": "citizens",
        "description": "Kunduzgi ovoz berishda uning ovozi 2 ta hisoblanadi.",
    },
    "rushatxon": {
        "name": "Ayg'oqchi",
        "icon": "🔍",
        "team": "citizens",
        "description": "Kechasi yashirincha ma'lumot toplaydi.",
    },
    "ruhiyatshunos": {
        "name": "Psixolog",
        "icon": "🧠",
        "team": "citizens",
        "description": "O'yinchining ruhiy holatini va uning yolg'on gapirayotganini aniqlaydi.",
    },
    # ==================== MAFIYA JAMOA (10 TA) ====================
    "don": {
        "name": "Don",
        "icon": "🤵🏻",
        "team": "mafia",
        "description": "Mafiya boshlig'i. Komissarni izlaydi va o'ldirishga buyruq beradi.",
    },
    "mafia": {
        "name": "Mafia",
        "icon": "🤵🏼",
        "team": "mafia",
        "description": "Don bilan birga kechasi tinch fuqarolarni yo'q qiladi.",
    },
    "kamikadze": {
        "name": "Bomba anjomchisi",
        "icon": "💣",
        "team": "mafia",
        "description": "O'zi bilan birga tinch fuqaroni portlatib yuborishi mumkin.",
    },
    "xoin": {
        "name": "Xoin",
        "icon": "🎭",
        "team": "mafia",
        "description": "Boshida fuqaro bo'lib ko'rinadi, mafiya o'lsa ularning o'rnini egallaydi.",
    },
    "lyutsifer": {
        "name": "Qora advokat",
        "icon": "🖤",
        "team": "mafia",
        "description": "Mafiya a'zolarini Komissar tekshiruvidan yashiradi.",
    },
    "pustosh": {
        "name": "Sodomsiz Mafia",
        "icon": "🕶️",
        "team": "mafia",
        "description": "O'zining rolingizni hech kimga bildiratmaydigan tajribali mafiya.",
    },
    "snayper": {
        "name": "Snayper",
        "icon": "🎯",
        "team": "mafia",
        "description": "Uzoq masofadan aniq zarba beruvchi mafiya merganchisi.",
    },
    "qopqonchi": {
        "name": "Tuzoqchi",
        "icon": "🪤",
        "team": "mafia",
        "description": "Tinch fuqarolar yo'liga tuzoq qo'yib ularning qobiliyatini bloklaydi.",
    },
    "sohib": {
        "name": "Mafioz xotini",
        "icon": "💃",
        "team": "mafia",
        "description": "Kechasi bir fuqaroni chalg'itib, uning yurishini to'xtatadi.",
    },
    "doktor_mafia": {
        "name": "Mafiya Shifokori",
        "icon": "💉",
        "team": "mafia",
        "description": "Faqat mafiya a'zolarini davolashga xizmat qiladi.",
    },
    # ==================== VAMPIRLAR JAMOA (4 TA) ====================
    "haqiqiy_vampir": {
        "name": "Haqiqiy vampir",
        "icon": "🧛🏻‍♂️",
        "team": "vampire",
        "description": "Vampirlar boshlig'i, o'yinchilarni tishlab o'z safiga qo'shadi.",
    },
    "vampir": {
        "name": "Vampir",
        "icon": "🧛🏻",
        "team": "vampire",
        "description": "Haqiqiy vampirga tunda ov qilishda yordam beradi.",
    },
    "graf": {
        "name": "Graf Drakula",
        "icon": "🦇",
        "team": "vampire",
        "description": "Kechasi qurbonini qonga aylantirib, maxsus qobiliyat beradi.",
    },
    "vampir_gipnoz": {
        "name": "Vampir Gipnozchi",
        "icon": "🌀",
        "team": "vampire",
        "description": "O'yinchini gipnoz qilib, uni noto'g'ri ovoz berishga majbur qiladi.",
    },
    # ==================== MUSTAQIL / NEYTRAL (8 TA) ====================
    "qotil": {
        "name": "Qotil (Maniyak)",
        "icon": "🔪",
        "team": "neutral",
        "description": "Yolg'iz o'ynaydi. Har kecha bitta kishini yo'q qiladi.",
    },
    "odamxo'r": {
        "name": "Odamxo'r (Zombi)",
        "icon": "🧟‍♂️",
        "team": "neutral",
        "description": "O'ldirgan kishisini zombiga aylantirib yuboradi.",
    },
    "Telba": {
        "name": "Telba (Ahmoq)",
        "icon": "🤡",
        "team": "neutral",
        "description": "Maqsadi — kunduzgi ovoz berishda o'zini osishga majbur qilish.",
    },
    "Aron": {
        "name": "Arvoh",
        "icon": "👻",
        "team": "neutral",
        "description": "O'lgandan keyin ham bir marta ovoz berish huquqiga ega bo'ladi.",
    },
    "Ratsar": {
        "name": "Ritsar",
        "icon": "⚔️",
        "team": "neutral",
        "description": "O'zining shaxsiy maqsadi bor, har qanday hujumdan 1 marta himoyalangan.",
    },
    "Anarxist": {
        "name": "Anarxist",
        "icon": "🔥",
        "team": "neutral",
        "description": "O'yin qoidalarini buzib, tasodifiy kishilarni o'yindan chiqaradi.",
    },
    "Xudo": {
        "name": "Omadli fuqaro",
        "icon": "🍀",
        "team": "neutral",
        "description": "Hech kim unga kechasi tega olmaydi, mutlaq daxlsiz.",
    },
    "O'g'ri": {
        "name": "O'g'ri",
        "icon": "🥷",
        "team": "neutral",
        "description": "Kechasi boshqa o'yinchining qobiliyatini o'g'irlab ishlatadi.",
    },
}
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# 1. Ro'yxatdan o'tish tugmasini yaratish
join_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎲 O'yinga qo'shilish", 
                callback_data="join_game"
            )
        ]
    ]
)

# 2. Guruhda o'yin va ro'yxatni boshlash
@dp.message(Command("start"))
async def start_game_cmd(message: Message):
    chat_id = message.chat.id

    # Guruhda registratsiya xabarini tugma bilan birga yuborish
    await message.answer(
        "🎲 **Nexus Mafia** oyunu uchun ro'yxatdan o'tish boshlandi!\n\n"
        "O'yinda qatnashish uchun quyidagi tugmani bosing:",
        reply_markup=join_keyboard,
        parse_mode="Markdown"
    )

# 3. Tugma bosilganda o'yinchini ro'yxatga olish
@dp.callback_query(F.data == "join_game")
async def join_game_callback(callback: CallbackQuery):
    user_name = callback.from_user.full_name

    # O'yinchiga ro'yxatdan o'tgani haqida bildirishnoma chiqarish
    await callback.answer(f"✅ {user_name}, siz ro'yxatdan o'tdingiz!", show_alert=True)
# roles.py - Nexus Mafia boti uchun 40 ta rol va ularning tavsiflari

ROLES = {
    # 🔴 MAFIYA VA YOMONLAR JAMOASI
    "mafia_boss": {
        "name": "🔪 Mafiya Bossi (Don)",
        "team": "mafia",
        "description": "Mafiya yetakchisi. Tunda otish qarorini beradi va Komissar tekshirganda 'Tinch aholi' bo'lib ko'rinadi."
    },
    "mafia": {
        "name": "🔪 Mafiya",
        "team": "mafia",
        "description": "Boss yo'qligida nishonni otadi va tungi ovoz berishda qatnashadi."
    },
    "lady": {
        "name": "💃 Tungi xonim",
        "team": "mafia",
        "description": "Har kecha bir o'yinchini tanlaydi va uning tungi qobiliyatini muzlatib qo'yadi."
    },
    "killer_mafia": {
        "name": "🔫 Snayper",
        "team": "mafia",
        "description": "Ma'lum bir o'qlari soniga ega bo'lib, xohlagan tunida istalgan o'yinchini o'ldirishi mumkin."
    },
    "lawyer": {
        "name": "📜 Advokat",
        "team": "mafia",
        "description": "Mafiya a'zosini tanlaydi va uni kunduzgi ovoz berishda osilishdan himoya qiladi."
    },
    "spy": {
        "name": "🕵️‍♂️ Xufyona ayg'oqchi",
        "team": "mafia",
        "description": "Har kecha bir o'yinchining aniq rolini bilib oladi."
    },
    "terrorist": {
        "name": "💣 Terrorchi",
        "team": "mafia",
        "description": "Kunduzi osilganda yoki tunda o'ldirilganda o'ziga qo'shib boshqa bir o'yinchini ham olib ketadi."
    },
    "mafia_doc": {
        "name": "👨‍⚕️ Mafiya Shifokori",
        "team": "mafia",
        "description": "Faqat Mafiya a'zolarini davolay oladigan xususiy tabib."
    },
    "chameleon": {
        "name": "🎭 Niqobchi",
        "team": "mafia",
        "description": "Komissar tekshirganda har safar har xil rol bo'lib ko'rinadi."
    },
    "moter": {
        "name": "🔇 Ovozsizlantiruvchi",
        "team": "mafia",
        "description": "Tanlangan o'yinchining ertangi kuni guruhda yozishiga taqiq qo'yadi."
    },

    # 🟢 TINCH AHOLI VA EZGULIK JAMOASI
    "civilian": {
        "name": "🏡 Tinch aholi",
        "team": "civilian",
        "description": "Kunduzi muhokama qiladi va ovoz beradi."
    },
    "doctor": {
        "name": "🩺 Shifokor",
        "team": "civilian",
        "description": "Har kecha bir kishini davolaydi va o'limdan qutqaradi."
    },
    "commissar": {
        "name": "🕵️ Komissar",
        "team": "civilian",
        "description": "Har kecha bir o'yinchining mafiya yoki tinch ekanligini tekshiradi."
    },
    "bodyguard": {
        "name": "🛡️ Tansoqchi",
        "team": "civilian",
        "description": "Har kecha bir kishini o'z jonini xatarga qo'yib himoya qiladi."
    },
    "sergeant": {
        "name": "🏹 Oltin O'q",
        "team": "civilian",
        "description": "Komissar o'lgandan keyin uning o'rniga o'tadi."
    },
    "wizard": {
        "name": "🔮 Baqshi",
        "team": "civilian",
        "description": "Har kecha o'yinchining aniq rolini bilishi mumkin."
    },
    "judge": {
        "name": "⚖️ Qozikalon",
        "team": "civilian",
        "description": "Kunduzgi ovoz berish natijasini bir martalikka bekor qilish huquqiga ega."
    },
    "enchanter": {
        "name": "🧙‍♂️ Afsunxona egasi",
        "team": "civilian",
        "description": "Tunda tanlangan kishiga 1 kechalik o'limga chidamlilik qalqoni beradi."
    },
    "journalist": {
        "name": "📝 Jurnalist",
        "team": "civilian",
        "description": "Har kecha ikki o'yinchini tanlaydi va ularning bir jamoadami yoki yo'qligini aniqlaydi."
    },
    "guest": {
        "name": "🛌 Tungi mehmon",
        "team": "civilian",
        "description": "Tunda kimningdir uyida tunaydi va unga qilingan hujumlardan omon qoladi."
    },
    "politician": {
        "name": "📢 Tashviqotchi",
        "team": "civilian",
        "description": "Ovoz berishda uning 1 ta ovozi 2 ta ovoz o'rniga o'tadi."
    },
    "saint": {
        "name": "🕯️ Avliyo",
        "team": "civilian",
        "description": "Agar kunduzi noto'g'ri osib o'ldirilsa, uni osganlar ertasi kuni ovoz bera olmaydi."
    },
    "engineer": {
        "name": "🛠️ Muhandis",
        "team": "civilian",
        "description": "Kechasi shifokor va tansoqchi qobiliyatidan xabardor bo'lib turadi."
    },
    "detective": {
        "name": "🧩 Detektiv",
        "team": "civilian",
        "description": "Har kecha kim kimning uyiga borganini kuzatadi."
    },
    "chemist": {
        "name": "💉 Kimyogar",
        "team": "civilian",
        "description": "O'yinchini zaharlab, uni keyingi kechada o'ladigan qilib qo'yadi."
    },
    "hypnotist": {
        "name": "🧠 Gipnozchi",
        "team": "civilian",
        "description": "O'yinchining tungi maqsadini boshqa tarafga yo'naltirib yuboradi."
    },
    "knight": {
        "name": "🛡️ Ritsar",
        "team": "civilian",
        "description": "Faqat bir marta mafiyaning otishidan omon qoladi."
    },
    "falcon": {
        "name": "🦅 Lochin",
        "team": "civilian",
        "description": "Tunda qaysi o'yinchi kimga hujum qilganini ko'rib turadi."
    },
    "heir": {
        "name": "📜 Merosxo'r",
        "team": "civilian",
        "description": "O'yinda birinchi bo'lib o me'yordan chiqqan yaxshi rolning qobiliyatini o'ziga oladi."
    },
    "gatekeeper": {
        "name": "🔔 Darvoza qorovuli",
        "team": "civilian",
        "description": "Tunda qaysi o'yinchilar ko'chaga chiqqanini sezadi."
    },

    # 🟡 NEUTRAL (YOLG'IZ) JAMOA
    "maniac": {
        "name": "🔪 Maniyak",
        "team": "neutral",
        "description": "Har kecha xohlagan kishisini o'ldiradi. Maqsadi — yagona g'olib bo'lish."
    },
    "joker": {
        "name": "🤡 Joker",
        "team": "neutral",
        "description": "Maqsadi — kunduzgi muhokamada o'zini osishlariga erishish. Osilsa, yutadi."
    },
    "zombie": {
        "name": "🧟 Zombi",
        "team": "neutral",
        "description": "Har kecha bir o'yinchini tishlab o'ziga o'xshash zombiga aylantiradi."
    },
    "thief": {
        "name": "🎭 O'g'ri",
        "team": "neutral",
        "description": "Har kecha bir o'yinchining rolini vaqtincha o'g'irlab ishlatadi."
    },
    "werewolf": {
        "name": "🐺 Bo'ri",
        "team": "neutral",
        "description": "Tinch aholi kabi yuradi, lekin har 2-kechada yirtqichga aylanadi."
    },
    "anarchist": {
        "name": "👑 Anarxist",
        "team": "neutral",
        "description": "Tunda ikkita o'yinchini bir-biriga bog'lab qo'yadi (biri o'lsa, ikkinchisi ham o'ladi)."
    },
    "hitman": {
        "name": "💰 Yollanma qotil",
        "team": "neutral",
        "description": "O'yin boshida berilgan nishonni yo'qotsa, g'olib bo'ladi."
    },
    "alien": {
        "name": "👽 O'zga sayyoralik",
        "team": "neutral",
        "description": "Barcha tirik o'yinchilarga belgi qo'ya olsa, yakka g'olib bo'ladi."
    },
    "fireman": {
        "name": "🧯 O't o'chiruvchi",
        "team": "neutral",
        "description": "Kerosin sepib chiqadi va bir kechada hammasini yoqib yuborishi mumkin."
    },
    "time_traveler": {
        "name": "⏳ Vaqt Sayyohi",
        "team": "neutral",
        "description": "O'yinda halok bo'lgan o'yinchilardan birini tiriltirish imkoniga ega."
    }
}

def get_roles_text():
    """/roles buyrug'i uchun chiroyli matn chiqaruvchi funksiya"""
    text = "🎭 **O'yin rollari (Jami 40 ta):**\n\n"
    
    text += "🔴 **Mafiya va Yomonlar:**\n"
    for role_id, info in ROLES.items():
        if info["team"] == "mafia":
            text += f"{info['name']} — {info['description']}\n"
            
    text += "\n🟢 **Tinch aholi va Ezgulik:**\n"
    for role_id, info in ROLES.items():
        if info["team"] == "civilian":
            text += f"{info['name']} — {info['description']}\n"
            
    text += "\n🟡 **Nötr va Yolg'izlar:**\n"
    for role_id, info in ROLES.items():
        if info["team"] == "neutral":
            text += f"{info['name']} — {info['description']}\n"
            
    return text
