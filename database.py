import aiosqlite
import time

DB_PATH = "winstars.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                photo_url TEXT,
                balance INTEGER DEFAULT 250,
                total_bets INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                profit INTEGER DEFAULT 0
            )
        """)
        
        # Таблица ставок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                match_title TEXT,
                outcome TEXT,
                coefficient REAL,
                amount INTEGER,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица чата
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                first_name TEXT,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Промокоды
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                reward_stars INTEGER,
                bonus_percent INTEGER
            )
        """)
        
        # Дефолтный промокод для теста
        await db.execute("INSERT OR IGNORE INTO promo_codes VALUES ('WINSTARS', 100, 10)")
        await db.commit()

async def get_or_create_user(user_id: int, username: str, first_name: str, photo_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    "INSERT INTO users (user_id, username, first_name, photo_url) VALUES (?, ?, ?, ?)",
                    (user_id, username, first_name, photo_url)
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as c2:
                    user = await c2.fetchone()
            return dict(user)

async def update_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def add_chat_message(user_id: int, first_name: str, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_messages (user_id, first_name, text) VALUES (?, ?, ?)",
            (user_id, first_name, text)
        )
        await db.commit()

async def get_chat_history(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT first_name, text FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in reversed(rows)]
