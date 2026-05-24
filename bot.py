from __future__ import annotations

import asyncio
import html
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)


BASE_DIR: Final[Path] = Path(__file__).resolve().parent
DB_PATH: Final[Path] = BASE_DIR / "game.db"
MINES_MULTIPLIERS: Final[list[float]] = [1.18, 1.52, 2.05, 3.10]
EMOJI_DEFAULTS: Final[dict[str, str]] = {
    "up": "📈",
    "down": "📉",
    "mine": "💣",
    "coin": "🪙",
    "work": "🛠",
    "gem": "💎",
    "explosion": "💥",
    "hidden": "⬛",
    "empty": "▫️",
}


JOBS: Final[dict[str, dict[str, object]]] = {
    "courier": {
        "title": "Курьер",
        "flavor": "Возишь серые пакеты по району.",
        "base": 110,
        "xp": 18,
        "aliases": ("курьер",),
        "drops": [("Энерджи", 0.30), ("Чаевые", 0.20)],
    },
    "miner": {
        "title": "Шахтёр",
        "flavor": "Бьёшь жилу и тащишь руду наверх.",
        "base": 135,
        "xp": 16,
        "aliases": ("шахта", "шахтер", "шахтёр"),
        "drops": [
            ("Руда", 0.35),
            ("Звёздный нефрит", 0.16),
            ("Обсидиан", 0.14),
            ("Редкий кристалл", 0.10),
        ],
    },
    "trader": {
        "title": "Трейдер",
        "flavor": "Ловишь импульс и продаёшь страх толпе.",
        "base": 125,
        "xp": 20,
        "aliases": ("трейдер",),
        "drops": [("Инсайд", 0.18), ("Флешка с графиками", 0.22)],
    },
    "hacker": {
        "title": "Хакер",
        "flavor": "Крутишь схемы, прокси и грязные логи.",
        "base": 145,
        "xp": 17,
        "aliases": ("хакер",),
        "drops": [("Прокси-ключ", 0.24), ("Эксплойт", 0.08)],
    },
}


def resolve_job_key(raw_value: str) -> str | None:
    normalized = raw_value.strip().lower()
    if normalized in JOBS:
        return normalized
    for key, data in JOBS.items():
        aliases = tuple(str(alias).lower() for alias in data.get("aliases", ()))
        if normalized in aliases:
            return key
    return None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def env_int_tuple(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    result: list[int] = []
    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item:
            continue
        result.append(int(item))
    return tuple(result) if result else default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    admin_ids: tuple[int, ...]
    default_work_reward: int
    work_cooldown_seconds: int
    trade_min_bet: int
    mines_min_bet: int
    house_edge_percent: int
    premium_emoji_up_id: str
    premium_emoji_down_id: str
    premium_emoji_mine_id: str
    premium_emoji_coin_id: str
    premium_emoji_work_id: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file(BASE_DIR / ".env")
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is not set")
        return cls(
            bot_token=token,
            bot_username=os.getenv("BOT_USERNAME", "game_bot").strip(),
            admin_ids=env_int_tuple("ADMIN_IDS", (8640643001,)),
            default_work_reward=env_int("DEFAULT_WORK_REWARD", 120),
            work_cooldown_seconds=env_int("WORK_COOLDOWN_SECONDS", 1800),
            trade_min_bet=env_int("TRADE_MIN_BET", 50),
            mines_min_bet=env_int("MINES_MIN_BET", 50),
            house_edge_percent=env_int("HOUSE_EDGE_PERCENT", 8),
            premium_emoji_up_id=os.getenv("PREMIUM_EMOJI_UP_ID", "").strip(),
            premium_emoji_down_id=os.getenv("PREMIUM_EMOJI_DOWN_ID", "").strip(),
            premium_emoji_mine_id=os.getenv("PREMIUM_EMOJI_MINE_ID", "").strip(),
            premium_emoji_coin_id=os.getenv("PREMIUM_EMOJI_COIN_ID", "").strip(),
            premium_emoji_work_id=os.getenv("PREMIUM_EMOJI_WORK_ID", "").strip(),
        )


@dataclass
class MinesSession:
    user_id: int
    chat_id: int
    message_id: int
    bet: int
    mine_index: int
    safe_picks: int
    opened_mask: int
    active: int


@dataclass
class AuctionListing:
    id: int
    seller_id: int
    item_name: str
    quantity: int
    price_per_unit: int
    created_at: int
    active: int


class EmojiSet:
    def __init__(self, settings: Settings) -> None:
        self._premium_ids = {
            "up": settings.premium_emoji_up_id,
            "down": settings.premium_emoji_down_id,
            "mine": settings.premium_emoji_mine_id,
            "coin": settings.premium_emoji_coin_id,
            "work": settings.premium_emoji_work_id,
        }

    @staticmethod
    def _custom(emoji_id: str, fallback: str) -> str:
        if emoji_id:
            safe_fallback = html.escape(fallback)
            return f'<tg-emoji emoji-id="{emoji_id}">{safe_fallback}</tg-emoji>'
        return fallback

    def render(self, name: str) -> str:
        fallback = EMOJI_DEFAULTS[name]
        emoji_id = self._premium_ids.get(name, "")
        return self._custom(emoji_id, fallback)

    def up(self) -> str:
        return self.render("up")

    def down(self) -> str:
        return self.render("down")

    def mine(self) -> str:
        return self.render("mine")

    def coin(self) -> str:
        return self.render("coin")

    def work(self) -> str:
        return self.render("work")

    def gem(self) -> str:
        return self.render("gem")

    def explosion(self) -> str:
        return self.render("explosion")

    def hidden(self) -> str:
        return self.render("hidden")

    def empty(self) -> str:
        return self.render("empty")


class GameDB:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER NOT NULL DEFAULT 1000,
                last_work_at INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                current_job TEXT NOT NULL DEFAULT 'courier'
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS game_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                game_type TEXT NOT NULL,
                bet INTEGER NOT NULL,
                payout INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS work_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_key TEXT NOT NULL,
                reward INTEGER NOT NULL,
                xp_gain INTEGER NOT NULL,
                item_drop TEXT,
                level_before INTEGER NOT NULL,
                level_after INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_name)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mines_sessions (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                bet INTEGER NOT NULL,
                mine_index INTEGER NOT NULL,
                safe_picks INTEGER NOT NULL DEFAULT 0,
                opened_mask INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auction_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price_per_unit INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self._ensure_user_columns()
        self.conn.commit()

    def _ensure_user_columns(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "xp" not in columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
        if "level" not in columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
        if "current_job" not in columns:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN current_job TEXT NOT NULL DEFAULT 'courier'"
            )

    def ensure_user(self, user_id: int, username: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = CASE
                    WHEN excluded.username = '' THEN users.username
                    ELSE excluded.username
                END
            """,
            (user_id, username or ""),
        )
        self.conn.commit()

    def get_user(self, user_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            """
            SELECT user_id, username, balance, last_work_at, xp, level, current_job
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"user {user_id} was not initialized")
        return row

    def add_balance(self, user_id: int, amount: int) -> int:
        self.conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        self.conn.commit()
        return int(self.get_user(user_id)["balance"])

    def set_last_work_at(self, user_id: int, timestamp: int) -> None:
        self.conn.execute(
            "UPDATE users SET last_work_at = ? WHERE user_id = ?",
            (timestamp, user_id),
        )
        self.conn.commit()

    def set_job(self, user_id: int, job_key: str) -> None:
        self.conn.execute(
            "UPDATE users SET current_job = ? WHERE user_id = ?",
            (job_key, user_id),
        )
        self.conn.commit()

    def add_xp(self, user_id: int, gained_xp: int) -> tuple[int, int, bool]:
        user = self.get_user(user_id)
        old_level = int(user["level"])
        new_xp = int(user["xp"]) + gained_xp
        new_level = xp_to_level(new_xp)
        self.conn.execute(
            "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
            (new_xp, new_level, user_id),
        )
        self.conn.commit()
        return new_xp, new_level, new_level > old_level

    def add_item(self, user_id: int, item_name: str, quantity: int = 1) -> None:
        self.conn.execute(
            """
            INSERT INTO inventory (user_id, item_name, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, item_name)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (user_id, item_name, quantity),
        )
        self.conn.commit()

    def remove_item(self, user_id: int, item_name: str, quantity: int = 1) -> bool:
        current = self.get_item_quantity(user_id, item_name)
        if current < quantity:
            return False
        self.conn.execute(
            """
            UPDATE inventory
            SET quantity = quantity - ?
            WHERE user_id = ? AND item_name = ?
            """,
            (quantity, user_id, item_name),
        )
        self.conn.execute(
            """
            DELETE FROM inventory
            WHERE user_id = ? AND item_name = ? AND quantity <= 0
            """,
            (user_id, item_name),
        )
        self.conn.commit()
        return True

    def get_item_quantity(self, user_id: int, item_name: str) -> int:
        row = self.conn.execute(
            """
            SELECT quantity
            FROM inventory
            WHERE user_id = ? AND item_name = ?
            """,
            (user_id, item_name),
        ).fetchone()
        return int(row["quantity"]) if row is not None else 0

    def get_inventory(self, user_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT item_name, quantity
            FROM inventory
            WHERE user_id = ? AND quantity > 0
            ORDER BY quantity DESC, item_name ASC
            """,
            (user_id,),
        ).fetchall()

    def log_game(self, user_id: int, game_type: str, bet: int, payout: int) -> None:
        self.conn.execute(
            """
            INSERT INTO game_log (user_id, game_type, bet, payout, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, game_type, bet, payout, int(time.time())),
        )
        self.conn.commit()

    def log_work(
        self,
        user_id: int,
        job_key: str,
        reward: int,
        xp_gain: int,
        item_drop: str | None,
        level_before: int,
        level_after: int,
        balance_after: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO work_log (
                user_id, job_key, reward, xp_gain, item_drop,
                level_before, level_after, balance_after, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                job_key,
                reward,
                xp_gain,
                item_drop,
                level_before,
                level_after,
                balance_after,
                int(time.time()),
            ),
        )
        self.conn.commit()

    def get_recent_work_logs(self, user_id: int, limit: int = 6) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, user_id, job_key, reward, xp_gain, item_drop,
                   level_before, level_after, balance_after, created_at
            FROM work_log
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    def get_active_mines_session(self, user_id: int) -> MinesSession | None:
        row = self.conn.execute(
            """
            SELECT user_id, chat_id, message_id, bet, mine_index, safe_picks, opened_mask, active
            FROM mines_sessions
            WHERE user_id = ? AND active = 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return MinesSession(**dict(row))

    def upsert_mines_session(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        bet: int,
        mine_index: int,
        safe_picks: int = 0,
        opened_mask: int = 0,
        active: int = 1,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO mines_sessions (
                user_id, chat_id, message_id, bet, mine_index, safe_picks, opened_mask, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                message_id = excluded.message_id,
                bet = excluded.bet,
                mine_index = excluded.mine_index,
                safe_picks = excluded.safe_picks,
                opened_mask = excluded.opened_mask,
                active = excluded.active
            """,
            (user_id, chat_id, message_id, bet, mine_index, safe_picks, opened_mask, active),
        )
        self.conn.commit()

    def update_mines_progress(self, user_id: int, safe_picks: int, opened_mask: int) -> None:
        self.conn.execute(
            """
            UPDATE mines_sessions
            SET safe_picks = ?, opened_mask = ?
            WHERE user_id = ?
            """,
            (safe_picks, opened_mask, user_id),
        )
        self.conn.commit()

    def close_mines_session(self, user_id: int) -> None:
        self.conn.execute(
            "UPDATE mines_sessions SET active = 0 WHERE user_id = ?",
            (user_id,),
        )
        self.conn.commit()

    def start_mines_session(self, session: MinesSession) -> bool:
        try:
            with self.conn:
                active_session = self.conn.execute(
                    """
                    SELECT 1
                    FROM mines_sessions
                    WHERE user_id = ? AND active = 1
                    """,
                    (session.user_id,),
                ).fetchone()
                if active_session is not None:
                    return False

                balance_row = self.conn.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (session.user_id,),
                ).fetchone()
                if balance_row is None or int(balance_row["balance"]) < session.bet:
                    return False

                self.conn.execute(
                    "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                    (session.bet, session.user_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO mines_sessions (
                        user_id, chat_id, message_id, bet, mine_index, safe_picks, opened_mask, active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        chat_id = excluded.chat_id,
                        message_id = excluded.message_id,
                        bet = excluded.bet,
                        mine_index = excluded.mine_index,
                        safe_picks = excluded.safe_picks,
                        opened_mask = excluded.opened_mask,
                        active = excluded.active
                    """,
                    (
                        session.user_id,
                        session.chat_id,
                        session.message_id,
                        session.bet,
                        session.mine_index,
                        session.safe_picks,
                        session.opened_mask,
                        session.active,
                    ),
                )
        except sqlite3.Error:
            return False
        return True

    def create_auction_listing(
        self,
        seller_id: int,
        item_name: str,
        quantity: int,
        price_per_unit: int,
    ) -> AuctionListing | None:
        try:
            with self.conn:
                stock = self.conn.execute(
                    """
                    SELECT quantity
                    FROM inventory
                    WHERE user_id = ? AND item_name = ?
                    """,
                    (seller_id, item_name),
                ).fetchone()
                if stock is None or int(stock["quantity"]) < quantity:
                    return None
                self.conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = quantity - ?
                    WHERE user_id = ? AND item_name = ?
                    """,
                    (quantity, seller_id, item_name),
                )
                self.conn.execute(
                    """
                    DELETE FROM inventory
                    WHERE user_id = ? AND item_name = ? AND quantity <= 0
                    """,
                    (seller_id, item_name),
                )
                cursor = self.conn.execute(
                    """
                    INSERT INTO auction_listings (
                        seller_id, item_name, quantity, price_per_unit, created_at, active
                    )
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (seller_id, item_name, quantity, price_per_unit, int(time.time())),
                )
                listing_id = int(cursor.lastrowid)
        except sqlite3.Error:
            return None
        return self.get_auction_listing(listing_id)

    def get_auction_listing(self, listing_id: int) -> AuctionListing | None:
        row = self.conn.execute(
            """
            SELECT id, seller_id, item_name, quantity, price_per_unit, created_at, active
            FROM auction_listings
            WHERE id = ?
            """,
            (listing_id,),
        ).fetchone()
        if row is None:
            return None
        return AuctionListing(**dict(row))

    def get_active_auction_listings(self, limit: int = 10) -> list[AuctionListing]:
        rows = self.conn.execute(
            """
            SELECT id, seller_id, item_name, quantity, price_per_unit, created_at, active
            FROM auction_listings
            WHERE active = 1
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [AuctionListing(**dict(row)) for row in rows]

    def buy_auction_listing(
        self,
        listing_id: int,
        buyer_id: int,
        quantity: int,
    ) -> tuple[bool, str, AuctionListing | None, int]:
        try:
            with self.conn:
                row = self.conn.execute(
                    """
                    SELECT id, seller_id, item_name, quantity, price_per_unit, created_at, active
                    FROM auction_listings
                    WHERE id = ? AND active = 1
                    """,
                    (listing_id,),
                ).fetchone()
                if row is None:
                    return False, "Лот не найден или уже закрыт.", None, 0

                listing = AuctionListing(**dict(row))
                if listing.seller_id == buyer_id:
                    return False, "Нельзя выкупить собственный лот.", listing, 0
                if quantity <= 0 or quantity > listing.quantity:
                    return False, "Некорректное количество для покупки.", listing, 0

                total_price = listing.price_per_unit * quantity
                buyer = self.conn.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (buyer_id,),
                ).fetchone()
                if buyer is None or int(buyer["balance"]) < total_price:
                    return False, "Недостаточно средств для покупки.", listing, 0

                self.conn.execute(
                    "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                    (total_price, buyer_id),
                )
                self.conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (total_price, listing.seller_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO inventory (user_id, item_name, quantity)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, item_name)
                    DO UPDATE SET quantity = quantity + excluded.quantity
                    """,
                    (buyer_id, listing.item_name, quantity),
                )

                remaining = listing.quantity - quantity
                if remaining <= 0:
                    self.conn.execute(
                        """
                        UPDATE auction_listings
                        SET quantity = 0, active = 0
                        WHERE id = ?
                        """,
                        (listing_id,),
                    )
                else:
                    self.conn.execute(
                        """
                        UPDATE auction_listings
                        SET quantity = ?
                        WHERE id = ?
                        """,
                        (remaining, listing_id),
                    )
        except sqlite3.Error:
            return False, "Не удалось провести сделку.", None, 0

        updated = self.get_auction_listing(listing_id)
        return True, "ok", updated, total_price


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def seconds_to_text(seconds: int) -> str:
    minutes, sec = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    if sec or not parts:
        parts.append(f"{sec}с")
    return " ".join(parts)


def parse_bet(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        bet = int(raw.replace("_", ""))
    except ValueError:
        return None
    return bet if bet > 0 else None


def parse_user_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        user_id = int(raw.strip())
    except ValueError:
        return None
    return user_id if user_id > 0 else None


def parse_positive_quantity(raw: str | None) -> int | None:
    return parse_bet(raw)


def xp_to_level(xp: int) -> int:
    return max(1, 1 + xp // 100)


def xp_to_next_level(level: int) -> int:
    return 100


def render_header(title: str, emoji: str) -> str:
    return f"{emoji} <b>{html.escape(title)}</b>"


def format_timestamp(timestamp: int) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(timestamp))


def render_profile_block(user: sqlite3.Row) -> str:
    job_key = str(user["current_job"])
    job = JOBS.get(job_key, JOBS["courier"])
    level = int(user["level"])
    xp = int(user["xp"])
    current_band = xp - ((level - 1) * 100)
    return (
        f"Уровень: <b>{level}</b> ({current_band}/{xp_to_next_level(level)})\n"
        f"Работа: <b>{html.escape(str(job['title']))}</b>"
    )


def render_work_log_entry(row: sqlite3.Row) -> str:
    job = JOBS.get(str(row["job_key"]), JOBS["courier"])
    parts = [
        f"• <b>{format_timestamp(int(row['created_at']))}</b> - {html.escape(str(job['title']))}",
        f"+{format_money(int(row['reward']))} кэша, +{int(row['xp_gain'])} XP",
    ]
    if int(row["level_after"]) > int(row["level_before"]):
        parts.append(f"lvl {int(row['level_before'])} -> {int(row['level_after'])}")
    if row["item_drop"]:
        parts.append(f"лут: {html.escape(str(row['item_drop']))}")
    parts.append(f"баланс: {format_money(int(row['balance_after']))}")
    return " | ".join(parts)


def render_balance_text(user: sqlite3.Row, emoji: EmojiSet) -> str:
    return (
        f"{render_header('Баланс', emoji.coin())}\n"
        f"Кэш: <b>{format_money(int(user['balance']))}</b>\n"
        f"{render_profile_block(user)}"
    )


def render_jobs_text(user: sqlite3.Row, emoji: EmojiSet) -> str:
    lines = [render_header("Профессии", emoji.work())]
    for key, data in JOBS.items():
        mark = "▸" if key == str(user["current_job"]) else "•"
        aliases = ", ".join(
            f"<code>{html.escape(str(alias))}</code>" for alias in data.get("aliases", ())
        )
        alias_suffix = f" (или {aliases})" if aliases else ""
        lines.append(
            f"{mark} <code>{key}</code> - <b>{html.escape(str(data['title']))}</b>, "
            f"{html.escape(str(data['flavor']))}{alias_suffix}"
        )
    lines.append("\nСменить можно кнопками ниже.")
    return "\n".join(lines)


def render_inventory_text(items: list[sqlite3.Row], emoji: EmojiSet) -> str:
    if not items:
        return f"{render_header('Инвентарь', emoji.coin())}\nПока пусто. Загляни в работу."
    lines = [render_header("Инвентарь", emoji.coin())]
    for item in items[:12]:
        lines.append(f"• {html.escape(str(item['item_name']))} x<b>{int(item['quantity'])}</b>")
    return "\n".join(lines)


def render_auction_text(listings: list[AuctionListing], emoji: EmojiSet) -> str:
    lines = [render_header("Аукцион", emoji.coin())]
    if not listings:
        lines.append("Активных лотов нет. Первый лот можно выставить вручную.")
        return "\n".join(lines)

    for listing in listings:
        total = listing.quantity * listing.price_per_unit
        lines.append(
            f"• Лот <code>#{listing.id}</code> - {html.escape(listing.item_name)} x<b>{listing.quantity}</b> "
            f"по <b>{format_money(listing.price_per_unit)}</b> за шт. "
            f"(всего {format_money(total)}), продавец <code>{listing.seller_id}</code>"
        )
    lines.append("\nПокупка и выставление сейчас доступны через ручной ввод.")
    return "\n".join(lines)


def render_worklog_text(target_user_id: int, rows: list[sqlite3.Row], emoji: EmojiSet) -> str:
    if not rows:
        return (
            f"{render_header('Логи смен', emoji.work())}\n"
            f"Для пользователя <code>{target_user_id}</code> записей пока нет."
        )
    lines = [
        render_header("Логи смен", emoji.work()),
        f"Пользователь: <code>{target_user_id}</code>",
    ]
    for row in rows:
        lines.append(render_work_log_entry(row))
    return "\n".join(lines)


def input_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Баланс"), KeyboardButton(text="Работа")],
            [KeyboardButton(text="Игры"), KeyboardButton(text="Профессии")],
            [KeyboardButton(text="Инвентарь"), KeyboardButton(text="Аукцион")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел или введи команду",
    )


def games_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Трейд", callback_data="game:trade"),
            ],
            [
                InlineKeyboardButton(text="Мины", callback_data="game:mines"),
            ],
        ]
    )


def replay_keyboard(game_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сыграть снова", callback_data=f"game:{game_name}")],
            [InlineKeyboardButton(text="Закрыть", callback_data="menu:close")],
        ]
    )


def trade_direction_keyboard(bet: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поставить вверх", callback_data=f"trade:dir:{bet}:up")],
            [InlineKeyboardButton(text="Поставить вниз", callback_data=f"trade:dir:{bet}:down")],
            [InlineKeyboardButton(text="Закрыть", callback_data="menu:close")],
        ]
    )


def jobs_keyboard(current_job: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, data in JOBS.items():
        prefix = "▸ " if key == current_job else ""
        rows.append(
            [InlineKeyboardButton(
                text=f"{prefix}{data['title']}",
                callback_data=f"job:set:{key}",
            )]
        )
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trade_round(bet: int, direction: str, house_edge_percent: int) -> tuple[bool, int, float]:
    edge = min(max(house_edge_percent / 200, 0.0), 0.2)
    won = random.random() < (0.5 - edge)
    delta_magnitude = round(random.uniform(0.15, 4.8), 2)
    if direction == "up":
        delta = delta_magnitude if won else -delta_magnitude
    else:
        delta = -delta_magnitude if won else delta_magnitude
    payout = max(1, round(bet * abs(delta) / 100))
    if not won:
        payout = -payout
    return won, payout, delta


def roll_job_reward(user: sqlite3.Row, runtime_settings: Settings) -> tuple[int, int, str, str | None]:
    job_key = str(user["current_job"])
    job = JOBS.get(job_key, JOBS["courier"])
    level = int(user["level"])
    base = int(job["base"])
    xp_gain = int(job["xp"]) + random.randint(2, 7)
    level_bonus = 1 + ((level - 1) * 0.08)
    volatility = random.uniform(0.85, 1.45)
    payout = int((base + runtime_settings.default_work_reward * 0.25) * level_bonus * volatility)
    flavor = str(job["flavor"])
    item_drop = None
    for item_name, chance in job["drops"]:
        if random.random() <= float(chance):
            item_drop = str(item_name)
            break
    return payout, xp_gain, flavor, item_drop


def mines_multiplier(safe_picks: int) -> float:
    if safe_picks <= 0:
        return 1.0
    capped = min(safe_picks, len(MINES_MULTIPLIERS))
    return MINES_MULTIPLIERS[capped - 1]


def mines_keyboard(
    session: MinesSession,
    emoji: EmojiSet,
    revealed: bool = False,
    exploded: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row_index in range(0, 5, 2):
        row_buttons: list[InlineKeyboardButton] = []
        for idx in range(row_index, min(row_index + 2, 5)):
            opened = bool(session.opened_mask & (1 << idx))
            if revealed and idx == session.mine_index:
                label = emoji.explosion()
                callback = "mines:locked"
            elif opened:
                label = emoji.gem()
                callback = "mines:locked"
            else:
                label = emoji.hidden() if not exploded else emoji.empty()
                callback = f"mines:open:{idx}" if session.active else "mines:locked"
            row_buttons.append(InlineKeyboardButton(text=label, callback_data=callback))
        rows.append(row_buttons)

    if session.active:
        cashout_label = "Забрать банк"
        if session.safe_picks > 0:
            cashout_amount = int(session.bet * mines_multiplier(session.safe_picks))
            cashout_label = f"Забрать {format_money(cashout_amount)}"
        rows.append(
            [InlineKeyboardButton(text=cashout_label, callback_data="mines:cashout")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


router = Router()
settings: Settings | None = None
emoji_set: EmojiSet | None = None
db: GameDB | None = None
pending_game_actions: dict[int, dict[str, int | str]] = {}


def get_runtime() -> tuple[Settings, EmojiSet, GameDB]:
    if settings is None or emoji_set is None or db is None:
        raise RuntimeError("Runtime is not initialized")
    return settings, emoji_set, db


def is_admin(user_id: int, runtime_settings: Settings) -> bool:
    return user_id in runtime_settings.admin_ids


def build_inline_article(
    article_id: str,
    title: str,
    description: str,
    message_text: str,
) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=article_id,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=message_text,
            parse_mode=ParseMode.HTML,
        ),
    )


def perform_work_action(user_id: int, username: str | None) -> tuple[str, bool]:
    runtime_settings, emoji, db = get_runtime()
    db.ensure_user(user_id, username)
    user = db.get_user(user_id)
    job_key = str(user["current_job"])
    now = int(time.time())
    next_available = int(user["last_work_at"]) + runtime_settings.work_cooldown_seconds
    if now < next_available:
        wait_for = seconds_to_text(next_available - now)
        return (
            f"{render_header('Смена закрыта', emoji.work())}\n"
            f"Ты уже в кэше. Возвращайся через <b>{wait_for}</b>.",
            False,
        )

    reward, xp_gain, flavor, item_drop = roll_job_reward(user, runtime_settings)
    old_level = int(user["level"])
    new_balance = db.add_balance(user_id, reward)
    db.set_last_work_at(user_id, now)
    _, new_level, leveled_up = db.add_xp(user_id, xp_gain)
    if item_drop is not None:
        db.add_item(user_id, item_drop)
    db.log_game(user_id, "work", 0, reward)
    db.log_work(
        user_id=user_id,
        job_key=job_key,
        reward=reward,
        xp_gain=xp_gain,
        item_drop=item_drop,
        level_before=old_level,
        level_after=new_level,
        balance_after=new_balance,
    )

    user_after = db.get_user(user_id)
    lines = [
        render_header("Смена завершена", emoji.work()),
        html.escape(flavor),
        f"Доход: <b>+{format_money(reward)}</b>",
        f"XP: <b>+{xp_gain}</b>",
        f"{emoji.coin()} Получено: <b>+{format_money(reward)}</b>",
        f"Уровень: <b>{int(user_after['level'])}</b>",
    ]
    if item_drop is not None:
        lines.append(f"Лут: <b>{html.escape(item_drop)}</b>")
    if leveled_up:
        lines.append(f"Новый ранг: <b>уровень {new_level}</b>")
    return "\n".join(lines), True


def perform_trade_action(
    user_id: int,
    username: str | None,
    bet: int,
    direction: str | None,
) -> str:
    runtime_settings, emoji, db = get_runtime()
    db.ensure_user(user_id, username)
    if bet < runtime_settings.trade_min_bet:
        return f"Минимальная ставка для трейда: <b>{runtime_settings.trade_min_bet}</b>"
    if direction is None:
        direction = random.choice(["up", "down"])
    if direction not in {"up", "down"}:
        return "Направление только <code>up</code> или <code>down</code>."

    user = db.get_user(user_id)
    balance = int(user["balance"])
    if balance < bet:
        return f"Недостаточно средств. Баланс: <b>{format_money(balance)}</b>"

    won, payout, delta = trade_round(bet, direction, runtime_settings.house_edge_percent)
    db.add_balance(user_id, payout)
    db.log_game(user_id, "trade", bet, payout)
    direction_emoji = emoji.up() if direction == "up" else emoji.down()
    direction_label = "up" if direction == "up" else "down"
    result_line = "Импульс пойман." if won else "Ликвидность вынесла твою сторону."
    change_sign = "+" if delta >= 0 else ""
    money_sign = "+" if payout >= 0 else ""
    flow_label = "Получено" if payout >= 0 else "Потрачено"
    return (
        f"{render_header('Сделка закрыта', direction_emoji)}\n"
        f"Ставка: <b>{format_money(bet)}</b>\n"
        f"Сценарий: <b>{direction_label}</b>\n"
        f"Свеча: <b>{change_sign}{delta:.2f}%</b>\n"
        f"P&amp;L: <b>{money_sign}{format_money(payout)}</b>\n"
        f"{result_line}\n"
        f"{emoji.coin()} {flow_label}: <b>{money_sign}{format_money(payout)}</b>"
    )


def prepare_mines_action(
    user_id: int,
    username: str | None,
    chat_id: int,
    bet: int,
) -> tuple[str, MinesSession | None]:
    runtime_settings, emoji, db = get_runtime()
    db.ensure_user(user_id, username)
    if bet < runtime_settings.mines_min_bet:
        return f"Минимальная ставка для мин: <b>{runtime_settings.mines_min_bet}</b>", None

    existing = db.get_active_mines_session(user_id)
    if existing is not None:
        return "У тебя уже открыта сессия мин. Доиграй её или забери банк в старом сообщении.", None

    user = db.get_user(user_id)
    balance = int(user["balance"])
    if balance < bet:
        return f"Недостаточно средств. Баланс: <b>{format_money(balance)}</b>", None

    mine_index = random.randint(0, 4)
    temp_session = MinesSession(
        user_id=user_id,
        chat_id=chat_id,
        message_id=0,
        bet=bet,
        mine_index=mine_index,
        safe_picks=0,
        opened_mask=0,
        active=1,
    )
    text = (
        f"{render_header('Минное поле открыто', emoji.mine())}\n"
        f"Ставка списана: <b>{format_money(bet)}</b>\n"
        "Открывай клетки. После каждой безопасной клетки можешь забрать банк."
    )
    return text, temp_session


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    runtime_settings, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    user = db.get_user(message.from_user.id)
    text = (
        f"{render_header('Подпольный терминал запущен', emoji.coin())}\n"
        f"Стартовый баланс: <b>{format_money(int(user['balance']))}</b>\n"
        f"{render_profile_block(user)}\n\n"
        "Весь основной сценарий доступен через кнопки ниже.\n"
        "Игры, профессии, инвентарь и аукцион открываются из меню."
    )
    await message.answer(text, reply_markup=input_menu_keyboard())


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    runtime_settings, emoji, _ = get_runtime()
    admin_lines = ""
    if message.from_user is not None and is_admin(message.from_user.id, runtime_settings):
        admin_lines = (
            "\n\nАдминские команды и отладочные сценарии доступны отдельно."
        )
    text = (
        f"{render_header('Режимы', emoji.work())}\n"
        "Пользуйся кнопками внизу.\n"
        "Через меню доступны баланс, работа, профессии, игры, инвентарь и аукцион.\n"
        "В играх после выбора просто вводи сумму следующим сообщением."
        f"{admin_lines}"
    )
    await message.answer(text, reply_markup=input_menu_keyboard())


@router.message(Command("give"))
async def give_handler(message: Message) -> None:
    runtime_settings, emoji, db = get_runtime()
    assert message.from_user is not None
    if not is_admin(message.from_user.id, runtime_settings):
        await message.answer("Команда только для админа.")
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование: <code>/give 123456789 5000</code>")
        return

    target_user_id = parse_user_id(parts[1])
    amount = parse_bet(parts[2])
    if target_user_id is None:
        await message.answer("Некорректный user_id.")
        return
    if amount is None:
        await message.answer("Сумма должна быть положительным числом.")
        return

    db.ensure_user(target_user_id, None)
    new_balance = db.add_balance(target_user_id, amount)
    db.log_game(target_user_id, "admin_give", 0, amount)
    await message.answer(
        f"{render_header('Выдача выполнена', emoji.coin())}\n"
        f"Пользователь: <code>{target_user_id}</code>\n"
        f"Зачислено: <b>+{format_money(amount)}</b>\n"
        f"Новый баланс: <b>{format_money(new_balance)}</b>"
    )


@router.message(Command("resetwork"))
async def resetwork_handler(message: Message) -> None:
    runtime_settings, emoji, db = get_runtime()
    assert message.from_user is not None
    if not is_admin(message.from_user.id, runtime_settings):
        await message.answer("Команда только для админа.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: <code>/resetwork 123456789</code>")
        return

    target_user_id = parse_user_id(parts[1])
    if target_user_id is None:
        await message.answer("Некорректный user_id.")
        return

    db.ensure_user(target_user_id, None)
    db.set_last_work_at(target_user_id, 0)
    await message.answer(
        f"{render_header('Кулдаун сброшен', emoji.work())}\n"
        f"Пользователь: <code>{target_user_id}</code>\n"
        "Теперь он может сразу использовать <code>/work</code>."
    )


@router.message(Command("balance"))
@router.message(F.text.lower() == "баланс")
async def balance_handler(message: Message) -> None:
    _, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    user = db.get_user(message.from_user.id)
    await message.answer(render_balance_text(user, emoji), reply_markup=input_menu_keyboard())


@router.message(Command("worklog"))
@router.message(F.text.lower() == "логи смен")
async def worklog_handler(message: Message) -> None:
    runtime_settings, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    raw_text = (message.text or "").strip()
    parts = raw_text.split()

    target_user_id = message.from_user.id
    if raw_text.lower() != "логи смен" and len(parts) > 1:
        if not is_admin(message.from_user.id, runtime_settings):
            await message.answer("Чужие логи доступны только админу.")
            return
        parsed_user_id = parse_user_id(parts[1])
        if parsed_user_id is None:
            await message.answer("Некорректный user_id.")
            return
        target_user_id = parsed_user_id
        db.ensure_user(target_user_id, None)

    rows = db.get_recent_work_logs(target_user_id)
    await message.answer(
        render_worklog_text(target_user_id, rows, emoji),
        reply_markup=input_menu_keyboard(),
    )


@router.message(Command("job"))
@router.message(F.text.lower() == "профессии")
async def job_handler(message: Message) -> None:
    _, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    parts = (message.text or "").split(maxsplit=1)
    user = db.get_user(message.from_user.id)

    if len(parts) == 1:
        await message.answer(
            render_jobs_text(user, emoji),
            reply_markup=jobs_keyboard(str(user["current_job"])),
        )
        return

    job_key = resolve_job_key(parts[1])
    if job_key is None:
        await message.answer("Такой профессии нет. Выбери вариант из списка ниже.")
        return
    db.set_job(message.from_user.id, job_key)
    job = JOBS[job_key]
    await message.answer(
        f"{render_header('Профессия обновлена', emoji.work())}\n"
        f"Теперь ты: <b>{html.escape(str(job['title']))}</b>\n"
        f"{html.escape(str(job['flavor']))}",
        reply_markup=jobs_keyboard(job_key),
    )


@router.message(Command("inventory"))
@router.message(F.text.lower() == "инвентарь")
async def inventory_handler(message: Message) -> None:
    _, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    items = db.get_inventory(message.from_user.id)
    await message.answer(render_inventory_text(items, emoji), reply_markup=input_menu_keyboard())


@router.message(Command("auction"))
@router.message(F.text.lower() == "аукцион")
async def auction_handler(message: Message) -> None:
    _, emoji, db = get_runtime()
    listings = db.get_active_auction_listings()
    await message.answer(render_auction_text(listings, emoji), reply_markup=input_menu_keyboard())


@router.message(Command("auction_sell"))
async def auction_sell_handler(message: Message) -> None:
    _, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(
            "Использование: <code>/auction_sell Звёздный нефрит 2 500</code>",
            reply_markup=input_menu_keyboard(),
        )
        return

    quantity = parse_positive_quantity(parts[-2])
    price_per_unit = parse_bet(parts[-1])
    item_name = " ".join(parts[1:-2]).strip()
    if not item_name or quantity is None or price_per_unit is None:
        await message.answer(
            "Нужны корректные название, количество и цена за штуку.",
            reply_markup=input_menu_keyboard(),
        )
        return

    listing = db.create_auction_listing(
        seller_id=message.from_user.id,
        item_name=item_name,
        quantity=quantity,
        price_per_unit=price_per_unit,
    )
    if listing is None:
        stock = db.get_item_quantity(message.from_user.id, item_name)
        await message.answer(
            f"Не удалось выставить лот. У тебя этого предмета: <b>{stock}</b>.",
            reply_markup=input_menu_keyboard(),
        )
        return

    total = listing.quantity * listing.price_per_unit
    await message.answer(
        f"{render_header('Лот выставлен', emoji.coin())}\n"
        f"Лот: <code>#{listing.id}</code>\n"
        f"Предмет: <b>{html.escape(listing.item_name)}</b>\n"
        f"Количество: <b>{listing.quantity}</b>\n"
        f"Цена за шт.: <b>{format_money(listing.price_per_unit)}</b>\n"
        f"Всего: <b>{format_money(total)}</b>",
        reply_markup=input_menu_keyboard(),
    )


@router.message(Command("auction_buy"))
async def auction_buy_handler(message: Message) -> None:
    _, emoji, db = get_runtime()
    assert message.from_user is not None
    db.ensure_user(message.from_user.id, message.from_user.username)
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/auction_buy 12 2</code>",
            reply_markup=input_menu_keyboard(),
        )
        return

    listing_id = parse_bet(parts[1])
    quantity = parse_positive_quantity(parts[2]) if len(parts) > 2 else 1
    if listing_id is None or quantity is None:
        await message.answer(
            "Нужны корректные id лота и количество.",
            reply_markup=input_menu_keyboard(),
        )
        return

    success, result, listing, total_price = db.buy_auction_listing(
        listing_id=listing_id,
        buyer_id=message.from_user.id,
        quantity=quantity,
    )
    if not success:
        await message.answer(result, reply_markup=input_menu_keyboard())
        return

    balance = int(db.get_user(message.from_user.id)["balance"])
    remaining = listing.quantity if listing is not None else 0
    await message.answer(
        f"{render_header('Покупка закрыта', emoji.coin())}\n"
        f"Лот: <code>#{listing_id}</code>\n"
        f"Списано: <b>{format_money(total_price)}</b>\n"
        f"Новый баланс: <b>{format_money(balance)}</b>\n"
        f"Остаток в лоте: <b>{remaining}</b>",
        reply_markup=input_menu_keyboard(),
    )


@router.message(Command("emoji_debug"))
async def emoji_debug_handler(message: Message) -> None:
    await message.answer(
        "Пришли следующим сообщением premium/custom emoji.\n"
        "Я верну его <code>custom_emoji_id</code>, который потом можно вставить в <code>.env</code>."
    )


@router.message(Command("work"))
@router.message(F.text.lower() == "работа")
async def work_handler(message: Message) -> None:
    assert message.from_user is not None
    text, _ = perform_work_action(message.from_user.id, message.from_user.username)
    await message.answer(text, reply_markup=input_menu_keyboard())


@router.message(F.text.lower() == "игры")
async def games_menu_handler(message: Message) -> None:
    await message.answer(
        "Выбери мини-игру. После выбора просто вводи сумму. Если ошибёшься, можно сразу написать ещё раз.",
        reply_markup=games_keyboard(),
    )


@router.message(Command("trade"))
@router.message(F.text.startswith("/trade@"))
async def trade_handler(message: Message) -> None:
    runtime_settings, _, _ = get_runtime()
    assert message.from_user is not None
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/trade 200</code>",
            reply_markup=input_menu_keyboard(),
        )
        return

    bet = parse_bet(parts[1])
    direction = parts[2].lower() if len(parts) > 2 else None
    if bet is None:
        await message.answer("Ставка должна быть положительным числом.", reply_markup=input_menu_keyboard())
        return
    if bet < runtime_settings.trade_min_bet:
        await message.answer(
            f"Минимальная ставка для трейда: <b>{runtime_settings.trade_min_bet}</b>",
            reply_markup=input_menu_keyboard(),
        )
        return

    text = perform_trade_action(message.from_user.id, message.from_user.username, bet, direction)
    await message.answer(text, reply_markup=replay_keyboard("trade"))


@router.message(Command("mines"))
async def mines_handler(message: Message) -> None:
    assert message.from_user is not None
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: <code>/mines 300</code>", reply_markup=input_menu_keyboard())
        return

    bet = parse_bet(parts[1])
    if bet is None:
        await message.answer("Ставка должна быть положительным числом.", reply_markup=input_menu_keyboard())
        return

    text, temp_session = prepare_mines_action(
        message.from_user.id,
        message.from_user.username,
        message.chat.id,
        bet,
    )
    if temp_session is None:
        await message.answer(text, reply_markup=input_menu_keyboard())
        return
    _, emoji, db = get_runtime()
    sent = await message.answer(
        text,
        reply_markup=mines_keyboard(temp_session, emoji),
    )
    temp_session.message_id = sent.message_id
    if not db.start_mines_session(temp_session):
        await sent.edit_text(
            "Не удалось открыть сессию. Проверь баланс и попробуй снова.",
            reply_markup=None,
        )


@router.callback_query(F.data == "mines:locked")
async def mines_locked_handler(callback: CallbackQuery) -> None:
    await callback.answer("Эта клетка уже закрыта.")


@router.callback_query(F.data.startswith("mines:open:"))
async def mines_open_handler(callback: CallbackQuery) -> None:
    _, emoji, db = get_runtime()
    assert callback.from_user is not None
    session = db.get_active_mines_session(callback.from_user.id)
    if session is None:
        await callback.answer("Сессия не найдена.", show_alert=True)
        return
    if callback.message is None or callback.message.message_id != session.message_id:
        await callback.answer("Играй в актуальном сообщении.", show_alert=True)
        return

    cell_index = int(str(callback.data).split(":")[-1])
    if session.opened_mask & (1 << cell_index):
        await callback.answer("Эта клетка уже открыта.")
        return

    new_mask = session.opened_mask | (1 << cell_index)
    if cell_index == session.mine_index:
        session.opened_mask = new_mask
        session.active = 0
        db.update_mines_progress(session.user_id, session.safe_picks, new_mask)
        db.close_mines_session(session.user_id)
        db.log_game(session.user_id, "mines", session.bet, -session.bet)
        await callback.message.edit_text(
            f"{render_header('Подрыв', emoji.mine())}\n"
            f"Ставка сгорела: <b>{format_money(session.bet)}</b>\n"
            f"{emoji.coin()} Не твоя карта сегодня.",
            reply_markup=replay_keyboard("mines"),
        )
        await callback.answer("Мина.")
        return

    session.safe_picks += 1
    session.opened_mask = new_mask
    db.update_mines_progress(session.user_id, session.safe_picks, new_mask)
    current_cashout = int(session.bet * mines_multiplier(session.safe_picks))
    if session.safe_picks >= len(MINES_MULTIPLIERS):
        session.active = 0
        db.close_mines_session(session.user_id)
        db.add_balance(session.user_id, current_cashout)
        db.log_game(session.user_id, "mines", session.bet, current_cashout - session.bet)
        await callback.message.edit_text(
            f"{render_header('Поле зачищено', emoji.mine())}\n"
            f"Все безопасные клетки взяты.\n"
            f"Выплата: <b>{format_money(current_cashout)}</b>",
            reply_markup=replay_keyboard("mines"),
        )
        await callback.answer("Максимум взят.")
        return

    await callback.message.edit_text(
        f"{render_header('Поле живо', emoji.mine())}\n"
        f"Безопасных клеток: <b>{session.safe_picks}</b>\n"
        f"Текущий кэшаут: <b>{format_money(current_cashout)}</b>\n"
        "Лезешь дальше или фиксируешься?",
        reply_markup=mines_keyboard(session, emoji),
    )
    await callback.answer("Чисто.")


@router.callback_query(F.data == "mines:cashout")
async def mines_cashout_handler(callback: CallbackQuery) -> None:
    _, emoji, db = get_runtime()
    assert callback.from_user is not None
    session = db.get_active_mines_session(callback.from_user.id)
    if session is None:
        await callback.answer("Нет активной игры.", show_alert=True)
        return
    if callback.message is None or callback.message.message_id != session.message_id:
        await callback.answer("Это не та сессия.", show_alert=True)
        return
    if session.safe_picks == 0:
        await callback.answer("Сначала открой хотя бы одну безопасную клетку.", show_alert=True)
        return

    total_return = int(session.bet * mines_multiplier(session.safe_picks))
    db.close_mines_session(session.user_id)
    db.add_balance(session.user_id, total_return)
    db.log_game(session.user_id, "mines", session.bet, total_return - session.bet)
    session.active = 0
    await callback.message.edit_text(
        f"{render_header('Кэшаут', emoji.mine())}\n"
        f"Безопасных клеток: <b>{session.safe_picks}</b>\n"
        f"Возврат: <b>{format_money(total_return)}</b>\n"
        f"{emoji.coin()} Сделка по минам закрыта.",
        reply_markup=replay_keyboard("mines"),
    )
    await callback.answer("Банк забран.")


@router.callback_query(F.data == "menu:close")
async def close_menu_callback_handler(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Меню закрыто.")


@router.callback_query(F.data.startswith("trade:dir:"))
async def trade_direction_callback_handler(callback: CallbackQuery) -> None:
    assert callback.from_user is not None
    if callback.message is None:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    parts = str(callback.data).split(":")
    if len(parts) != 4:
        await callback.answer("Некорректная позиция.", show_alert=True)
        return

    bet = parse_bet(parts[2])
    direction = parts[3]
    if bet is None or direction not in {"up", "down"}:
        await callback.answer("Некорректная позиция.", show_alert=True)
        return

    text = perform_trade_action(
        callback.from_user.id,
        callback.from_user.username,
        bet,
        direction,
    )
    await callback.message.edit_text(text, reply_markup=replay_keyboard("trade"))
    await callback.answer("Позиция открыта.")


@router.callback_query(F.data.startswith("game:"))
async def game_select_callback_handler(callback: CallbackQuery) -> None:
    assert callback.from_user is not None
    if callback.message is None:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    action = str(callback.data)
    if action == "game:mines":
        pending_game_actions[callback.from_user.id] = {
            "action": "mines",
            "chat_id": callback.message.chat.id,
            "prompt_message_id": callback.message.message_id,
        }
        await callback.message.edit_text(
            "Мины выбраны.\nВведи сумму следующим сообщением, например: <code>300</code>"
        )
        await callback.answer("Жду сумму для мин.")
        return

    if action == "game:trade":
        pending_game_actions[callback.from_user.id] = {
            "action": "trade",
            "chat_id": callback.message.chat.id,
            "prompt_message_id": callback.message.message_id,
        }
        await callback.message.edit_text(
            "Трейд выбран.\nВведи сумму следующим сообщением, например: <code>200</code>"
        )
        await callback.answer("Жду сумму для трейда.")
        return

    await callback.answer("Неизвестная игра.", show_alert=True)


@router.callback_query(F.data.startswith("job:set:"))
async def set_job_callback_handler(callback: CallbackQuery) -> None:
    _, emoji, db = get_runtime()
    assert callback.from_user is not None
    db.ensure_user(callback.from_user.id, callback.from_user.username)
    job_key = str(callback.data).split(":")[-1]
    if job_key not in JOBS:
        await callback.answer("Такой профессии нет.", show_alert=True)
        return

    db.set_job(callback.from_user.id, job_key)
    job = JOBS[job_key]
    if callback.message is not None:
        user = db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            f"{render_header('Профессия обновлена', emoji.work())}\n"
            f"Теперь ты: <b>{html.escape(str(job['title']))}</b>\n"
            f"{html.escape(str(job['flavor']))}",
            reply_markup=jobs_keyboard(str(user["current_job"])),
        )
    await callback.answer("Профессия изменена.")


@router.message()
async def pending_game_amount_handler(message: Message) -> None:
    runtime_settings, _, _ = get_runtime()
    assert message.from_user is not None
    state = pending_game_actions.get(message.from_user.id)
    if state is None:
        return
    action = str(state["action"])

    bet = parse_bet(message.text)
    if bet is None:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(
            "Сумма должна быть положительным числом. Просто введи число ещё раз, например <code>300</code>.",
        )
        return

    min_bet = (
        runtime_settings.trade_min_bet if action == "trade" else runtime_settings.mines_min_bet
    )
    if bet < min_bet:
        try:
            await message.delete()
        except Exception:
            pass
        label = "трейда" if action == "trade" else "мин"
        await message.answer(f"Минимальная ставка для {label}: <b>{min_bet}</b>")
        return

    if action == "mines":
        pending_game_actions.pop(message.from_user.id, None)
        try:
            await message.delete()
        except Exception:
            pass
        prompt_message_id = int(state.get("prompt_message_id", 0))
        text, temp_session = prepare_mines_action(
            message.from_user.id,
            message.from_user.username,
            message.chat.id,
            bet,
        )
        if temp_session is None:
            await message.answer(text, reply_markup=input_menu_keyboard())
            return
        _, emoji, db = get_runtime()
        if prompt_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    text=text,
                    reply_markup=mines_keyboard(temp_session, emoji),
                )
                temp_session.message_id = prompt_message_id
            except Exception:
                sent = await message.answer(text, reply_markup=mines_keyboard(temp_session, emoji))
                temp_session.message_id = sent.message_id
        else:
            sent = await message.answer(text, reply_markup=mines_keyboard(temp_session, emoji))
            temp_session.message_id = sent.message_id
        if not db.start_mines_session(temp_session):
            if temp_session.message_id == prompt_message_id and prompt_message_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=prompt_message_id,
                        text="Не удалось открыть сессию. Проверь баланс и попробуй снова.",
                        reply_markup=replay_keyboard("mines"),
                    )
                except Exception:
                    await message.answer(
                        "Не удалось открыть сессию. Проверь баланс и попробуй снова.",
                        reply_markup=replay_keyboard("mines"),
                    )
            else:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=temp_session.message_id,
                    text="Не удалось открыть сессию. Проверь баланс и попробуй снова.",
                    reply_markup=replay_keyboard("mines"),
                )
        return

    if action == "trade":
        pending_game_actions.pop(message.from_user.id, None)
        try:
            await message.delete()
        except Exception:
            pass
        prompt_message_id = int(state.get("prompt_message_id", 0))
        if prompt_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    text=(
                        "Выбери позицию.\n"
                        f"Сумма: <b>{format_money(bet)}</b>\n"
                        "Куда ставишь?"
                    ),
                    reply_markup=trade_direction_keyboard(bet),
                )
                return
            except Exception:
                pass
        await message.answer(
            "Выбери позицию.",
            reply_markup=trade_direction_keyboard(bet),
        )
        return


@router.inline_query()
async def inline_query_handler(inline_query: InlineQuery) -> None:
    runtime_settings, emoji, db = get_runtime()
    db.ensure_user(inline_query.from_user.id, inline_query.from_user.username)
    user = db.get_user(inline_query.from_user.id)

    raw_query = (inline_query.query or "").strip().lower()
    query = raw_query or "all"
    job = JOBS.get(str(user["current_job"]), JOBS["courier"])
    results: list[InlineQueryResultArticle] = []

    if query in {"all", "balance", "bal"}:
        results.append(
            build_inline_article(
                "balance",
                "Баланс",
                f"{format_money(int(user['balance']))} | lvl {int(user['level'])} | {job['title']}",
                f"{render_header('Баланс', emoji.coin())}\n"
                f"Кэш: <b>{format_money(int(user['balance']))}</b>\n"
                f"{render_profile_block(user)}",
            )
        )

    if query in {"all", "work", "job", "shift"}:
        now = int(time.time())
        next_available = int(user["last_work_at"]) + runtime_settings.work_cooldown_seconds
        if now < next_available:
            work_description = f"Кулдаун: {seconds_to_text(next_available - now)}"
            work_text = (
                f"{render_header('Статус смены', emoji.work())}\n"
                f"Профессия: <b>{html.escape(str(job['title']))}</b>\n"
                f"Следующая смена через <b>{seconds_to_text(next_available - now)}</b>."
            )
        else:
            work_description = "Смена готова прямо сейчас"
            work_text = (
                f"{render_header('Статус смены', emoji.work())}\n"
                f"Профессия: <b>{html.escape(str(job['title']))}</b>\n"
                "Смена готова. Можно жать <code>/work</code>."
            )
        results.append(
            build_inline_article(
                "work",
                "Статус работы",
                work_description,
                work_text,
            )
        )

    if query in {"all", "jobs", "job"}:
        job_lines = [render_header("Профессии", emoji.work())]
        for key, data in JOBS.items():
            marker = "▸" if key == str(user["current_job"]) else "•"
            job_lines.append(
                f"{marker} <b>{html.escape(str(data['title']))}</b> - <code>{key}</code>"
            )
        results.append(
            build_inline_article(
                "jobs",
                "Профессии",
                f"Текущая: {job['title']}",
                "\n".join(job_lines),
            )
        )

    if query in {"all", "inventory", "inv"}:
        items = db.get_inventory(inline_query.from_user.id)
        if items:
            item_lines = [render_header("Инвентарь", emoji.coin())]
            for item in items[:8]:
                item_lines.append(
                    f"• {html.escape(str(item['item_name']))} x<b>{int(item['quantity'])}</b>"
                )
            inventory_text = "\n".join(item_lines)
            inventory_description = f"{len(items)} предметов"
        else:
            inventory_text = (
                f"{render_header('Инвентарь', emoji.coin())}\n"
                "Пока пусто. Предметы падают со смен и мини-игр."
            )
            inventory_description = "Пока пусто"
        results.append(
            build_inline_article(
                "inventory",
                "Инвентарь",
                inventory_description,
                inventory_text,
            )
        )

    if query in {"all", "logs", "worklog"}:
        logs = db.get_recent_work_logs(inline_query.from_user.id, limit=4)
        if logs:
            log_lines = [render_header("Последние смены", emoji.work())]
            for row in logs:
                log_lines.append(render_work_log_entry(row))
            log_text = "\n".join(log_lines)
            log_description = f"{len(logs)} последних смен"
        else:
            log_text = (
                f"{render_header('Последние смены', emoji.work())}\n"
                "Логов пока нет. Первая запись появится после <code>/work</code>."
            )
            log_description = "Логов пока нет"
        results.append(
            build_inline_article(
                "worklog",
                "Логи смен",
                log_description,
                log_text,
            )
        )

    if not results:
        results.append(
            build_inline_article(
                "help",
                "Подсказка",
                "Запросы: balance, work, jobs, inventory, logs",
                f"{render_header('Inline режим', emoji.coin())}\n"
                "Запросы: <code>balance</code>, <code>work</code>, <code>jobs</code>, "
                "<code>inventory</code>, <code>logs</code>.",
            )
        )

    await inline_query.answer(results, cache_time=1, is_personal=True)


@router.message(F.entities)
async def custom_emoji_debug_handler(message: Message) -> None:
    found_ids: list[str] = []
    for entity in message.entities or []:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            found_ids.append(entity.custom_emoji_id)

    if not found_ids:
        return

    lines = ["Найдены custom emoji id:"]
    for emoji_id in found_ids:
        lines.append(f"<code>{emoji_id}</code>")
    await message.answer("\n".join(lines))


async def main() -> None:
    global settings, emoji_set, db
    settings = Settings.from_env()
    emoji_set = EmojiSet(settings)
    db = GameDB(DB_PATH)
    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
