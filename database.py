"""SQLite storage for Puppy Points balances, transactions, and backups."""

import argparse
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile


DATABASE_PATH = Path(__file__).resolve().parent / "data" / "puppy_points.db"
PAGE_SIZE = 10
VALID_AMOUNTS = frozenset({1, 10, 50, 100, -1, -10, -50, -100})

SCHEMA = """
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS balances (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    balance INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    interaction_id TEXT NOT NULL UNIQUE,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount IN (1, 10, 50, 100, -1, -10, -50, -100)),
    previous_balance INTEGER NOT NULL,
    new_balance INTEGER NOT NULL CHECK (new_balance = previous_balance + amount),
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (guild_id, user_id) REFERENCES balances (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS balances_leaderboard
    ON balances (guild_id, balance DESC, length(user_id), user_id);

CREATE INDEX IF NOT EXISTS transactions_history
    ON transactions (guild_id, user_id, id DESC);
"""


@dataclass(frozen=True, slots=True)
class Transaction:
    id: int
    guild_id: int
    user_id: int
    staff_id: int
    amount: int
    previous_balance: int
    new_balance: int
    reason: str | None
    created_at: datetime


def _transaction(row: sqlite3.Row) -> Transaction:
    return Transaction(
        id=row["id"],
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        staff_id=int(row["staff_id"]),
        amount=row["amount"],
        previous_balance=row["previous_balance"],
        new_balance=row["new_balance"],
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class Database:
    def __init__(self, path: Path | str = DATABASE_PATH) -> None:
        self.path = Path(path).resolve()

    @contextmanager
    def _connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        # Discord IDs are strings in SQLite so the full unsigned snowflake fits.
        uri = self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rw")
        with closing(sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    def initialize(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            # A read-only backup running as root must never create WAL sidecars.
            if db.execute("PRAGMA journal_mode = DELETE").fetchone()[0] != "delete":
                raise sqlite3.OperationalError("Could not enable SQLite rollback journaling.")
            with db:
                db.executescript(SCHEMA)

    def change_points(
        self,
        guild_id: int,
        user_id: int,
        staff_id: int,
        amount: int,
        reason: str | None,
        interaction_id: int,
    ) -> tuple[Transaction, bool]:
        if type(amount) is not int or amount not in VALID_AMOUNTS:
            raise ValueError("Unsupported Puppy Points transaction amount.")

        guild, user, staff, interaction = map(str, (guild_id, user_id, staff_id, interaction_id))
        with self._connect() as db:
            # Lock writers before checking for a replay or reading the current balance.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM transactions WHERE interaction_id = ?", (interaction,)
            ).fetchone()
            if existing is not None:
                if (guild, user, staff, amount, reason) != (
                    existing["guild_id"], existing["user_id"], existing["staff_id"],
                    existing["amount"], existing["reason"],
                ):
                    raise ValueError("Discord interaction ID already belongs to another transaction.")
                return _transaction(existing), False

            row = db.execute(
                "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?", (guild, user)
            ).fetchone()
            previous = row["balance"] if row is not None else 0
            new = previous + amount
            created_at = datetime.now(timezone.utc)
            db.execute(
                """
                INSERT INTO balances (guild_id, user_id, balance) VALUES (?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET balance = excluded.balance
                """,
                (guild, user, new),
            )
            cursor = db.execute(
                """
                INSERT INTO transactions (
                    interaction_id, guild_id, user_id, staff_id, amount, previous_balance,
                    new_balance, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (interaction, guild, user, staff, amount, previous, new, reason, created_at.isoformat()),
            )
            return Transaction(
                id=cursor.lastrowid,
                guild_id=guild_id,
                user_id=user_id,
                staff_id=staff_id,
                amount=amount,
                previous_balance=previous,
                new_balance=new,
                reason=reason,
                created_at=created_at,
            ), True

    def points(self, guild_id: int, user_id: int) -> tuple[int, int | None]:
        guild, user = str(guild_id), str(user_id)
        with self._connect(read_only=True) as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?", (guild, user)
            ).fetchone()
            if row is None:
                return 0, None

            balance = row["balance"]
            outranking = db.execute(
                """
                SELECT COUNT(*) FROM balances
                WHERE guild_id = ?
                  AND (balance > ? OR (balance = ? AND (length(user_id), user_id) < (?, ?)))
                """,
                (guild, balance, balance, len(user), user),
            ).fetchone()[0]
            return balance, outranking + 1

    def leaderboard(self, guild_id: int, page: int) -> tuple[list[tuple[int, int]], int]:
        guild = str(guild_id)
        with self._connect(read_only=True) as db:
            db.execute("BEGIN")
            total = db.execute(
                "SELECT COUNT(*) FROM balances WHERE guild_id = ?", (guild,)
            ).fetchone()[0]
            rows = db.execute(
                """
                SELECT user_id, balance FROM balances WHERE guild_id = ?
                ORDER BY balance DESC, length(user_id), user_id LIMIT ? OFFSET ?
                """,
                (guild, PAGE_SIZE, page * PAGE_SIZE),
            ).fetchall()
            return [(int(row["user_id"]), row["balance"]) for row in rows], total

    def history(self, guild_id: int, user_id: int, page: int) -> tuple[list[Transaction], int]:
        guild, user = str(guild_id), str(user_id)
        with self._connect(read_only=True) as db:
            db.execute("BEGIN")
            total = db.execute(
                "SELECT COUNT(*) FROM transactions WHERE guild_id = ? AND user_id = ?", (guild, user)
            ).fetchone()[0]
            rows = db.execute(
                """
                SELECT * FROM transactions WHERE guild_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT ? OFFSET ?
                """,
                (guild, user, PAGE_SIZE, page * PAGE_SIZE),
            ).fetchall()
            return [_transaction(row) for row in rows], total

    def backup(self, destination: Path | str) -> None:
        destination = Path(destination).absolute()
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".puppy-points-", suffix=".tmp", delete=False
        ) as temp:
            temporary_path = Path(temp.name)

        try:
            with self._connect(read_only=True) as source:
                with closing(sqlite3.connect(temporary_path)) as copy:
                    source.backup(copy, pages=128)
                    if copy.execute("PRAGMA journal_mode = DELETE").fetchone()[0] != "delete":
                        raise sqlite3.OperationalError("Could not finalize the standalone backup.")
                    if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise sqlite3.DatabaseError("Backup did not pass SQLite integrity checks.")
            # Publish only a finished copy and leave every existing backup untouched.
            if os.name == "nt":
                temporary_path.rename(destination)
            else:
                os.link(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize the installed database")
    backup = commands.add_parser("backup", help="create a verified online database backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()

    database = Database()
    try:
        if args.command == "init":
            database.initialize()
        else:
            database.backup(args.destination)
    except (OSError, sqlite3.Error) as error:
        parser.exit(1, f"Database {args.command} failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
