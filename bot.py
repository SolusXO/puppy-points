"""Discord application entry point for Puppy Points."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3

import discord
from discord import app_commands

from database import Database, PAGE_SIZE, Transaction, VALID_AMOUNTS


LOG = logging.getLogger("puppy_points")
ASSETS = Path(__file__).resolve().parent / "assets"
IMAGES = {
    1: "plus_1.png",
    10: "plus_10.png",
    50: "plus_50.png",
    100: "plus_100.png",
    -1: "minus_1.png",
    -10: "minus_10.png",
    -50: "minus_50.png",
    -100: "minus_100.png",
}
NO_MENTIONS = discord.AllowedMentions.none()
DISCORD_ERRORS = (discord.DiscordException, OSError)
Renderer = Callable[[discord.Interaction, int], Awaitable[tuple[discord.Embed, int]]]


class SecretFormatter(logging.Formatter):
    def __init__(self, secret: str) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")
        self.secret = secret

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return message.replace(self.secret, "[redacted]") if self.secret else message


def verify_assets() -> None:
    missing = []
    invalid = []
    for name in IMAGES.values():
        path = ASSETS / name
        try:
            with path.open("rb") as image:
                if image.read(8) != b"\x89PNG\r\n\x1a\n" or not image.read(1):
                    invalid.append(name)
        except FileNotFoundError:
            missing.append(name)
        except OSError:
            invalid.append(name)
    if missing or invalid:
        problems = []
        if missing:
            problems.append("missing image assets: " + ", ".join(missing))
        if invalid:
            problems.append("unreadable or invalid PNG assets: " + ", ".join(invalid))
        raise ValueError(". ".join(problems))
    LOG.info("Verified all eight Puppy Points images")


def staff_roles(raw_roles: str) -> frozenset[int]:
    if not raw_roles.strip():
        return frozenset()
    role_ids = raw_roles.split(",")
    if any(not re.fullmatch(r"[0-9]{1,20}", value.strip()) for value in role_ids):
        raise ValueError("PUPPY_STAFF_ROLE_IDS must contain comma-separated Discord role IDs")
    roles = frozenset(int(value) for value in role_ids)
    if any(not 0 < role < 2**64 for role in roles):
        raise ValueError("PUPPY_STAFF_ROLE_IDS contains an invalid Discord role ID")
    return roles


def notify_systemd() -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.sendto(b"READY=1\nSTATUS=Connected to Discord and slash commands registered", address)


def point_text(amount: int, *, signed: bool = False) -> str:
    suffix = "point" if abs(amount) == 1 else "points"
    number = f"{amount:+,}" if signed else f"{amount:,}"
    return f"{number} {suffix}"


def embed_text(value: str, limit: int) -> str:
    text = discord.utils.escape_markdown(discord.utils.escape_mentions(value))
    encoded = text.encode("utf-16-le")
    if len(encoded) <= 2 * limit:
        return text
    return encoded[: 2 * (limit - 1)].decode("utf-16-le", errors="ignore").rstrip("\\") + "…"


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="🐾 Puppy Points", description=message, color=discord.Color.red())


async def private_error(interaction: discord.Interaction, message: str) -> None:
    embed = error_embed(message)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, allowed_mentions=NO_MENTIONS, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_MENTIONS, ephemeral=True)


def has_staff_access(interaction: discord.Interaction, role_ids: frozenset[int]) -> bool:
    member = interaction.user
    return isinstance(member, discord.Member) and (
        member.guild_permissions.administrator
        or any(role.id in role_ids for role in member.roles)
    )


async def require_staff(interaction: discord.Interaction, role_ids: frozenset[int]) -> bool:
    if has_staff_access(interaction, role_ids):
        return True
    LOG.warning("Staff access denied for user=%s guild=%s", interaction.user.id, interaction.guild_id)
    await private_error(interaction, "An administrator or configured staff role is required.")
    return False


class Pages(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, render: Renderer, total_pages: int,
                 staff_role_ids: frozenset[int] | None = None) -> None:
        super().__init__(timeout=180)
        self.author_id = interaction.user.id
        self.message_interaction = interaction
        self.render = render
        self.staff_role_ids = staff_role_ids
        self.page = 0
        self.total_pages = total_pages
        self.lock = asyncio.Lock()
        self.set_buttons()

    def set_buttons(self) -> None:
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id != interaction.user.id:
            await private_error(interaction, "Run the slash command to open your own pages.")
            return False
        return self.staff_role_ids is None or await require_staff(interaction, self.staff_role_ids)

    async def turn(self, interaction: discord.Interaction, step: int) -> None:
        await interaction.response.defer()
        async with self.lock:
            self.message_interaction = interaction
            page = self.page + step
            if not 0 <= page < self.total_pages:
                return
            embed, total_pages = await self.render(interaction, page)
            old_page, old_total = self.page, self.total_pages
            self.page, self.total_pages = page, total_pages
            self.set_buttons()
            try:
                await interaction.edit_original_response(embed=embed, view=self, allowed_mentions=NO_MENTIONS)
            except DISCORD_ERRORS:
                self.page, self.total_pages = old_page, old_total
                self.set_buttons()
                raise

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.turn(interaction, -1)

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.turn(interaction, 1)

    async def on_error(self, interaction: discord.Interaction, error: Exception,
                       item: discord.ui.Item) -> None:
        LOG.error("Could not turn page in guild=%s", interaction.guild_id, exc_info=error)
        try:
            await private_error(interaction, "The page could not be loaded. Please try again.")
        except DISCORD_ERRORS:
            LOG.warning("Could not send the pagination error to Discord")

    async def on_timeout(self) -> None:
        async with self.lock:
            self.previous.disabled = self.next_page.disabled = True
            try:
                await self.message_interaction.edit_original_response(view=self)
            except discord.NotFound:
                pass
            except DISCORD_ERRORS:
                LOG.warning("Could not disable expired pagination buttons")


def page_count(count: int) -> int:
    return max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)


def transaction_embed(transaction: Transaction) -> discord.Embed:
    awarded = transaction.amount > 0
    verb = "received" if awarded else "lost"
    embed = discord.Embed(
        title="🐾 Good Puppy Points Awarded!" if awarded else "🦴 Good Puppy Points Removed!",
        description=f"<@{transaction.user_id}> {verb} **{point_text(abs(transaction.amount))}**.",
        color=discord.Color.blue() if awarded else discord.Color.red(),
    )
    embed.add_field(name="Previous balance", value=point_text(transaction.previous_balance))
    embed.add_field(name="New balance", value=point_text(transaction.new_balance))
    embed.add_field(name="Awarded by" if awarded else "Removed by", value=f"<@{transaction.staff_id}>")
    if transaction.reason:
        embed.add_field(name="Reason", value=embed_text(transaction.reason, 1024), inline=False)
    embed.set_image(url=f"attachment://{IMAGES[transaction.amount]}")
    return embed


class PuppyBot(discord.Client):
    def __init__(self, database: Database, role_ids: frozenset[int]) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=NO_MENTIONS)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(PuppyCommands(database, role_ids))
        self.tree.error(self.command_error)
        self.startup_ready = False

    async def setup_hook(self) -> None:
        registered = await self.tree.sync()
        if [command.name for command in registered] != ["puppy"]:
            raise RuntimeError("Discord did not confirm the global /puppy command")
        LOG.info("Registered global /puppy slash commands")

    async def on_ready(self) -> None:
        LOG.info("Connected to Discord as bot user %s", self.user.id)
        if not self.startup_ready:
            try:
                notify_systemd()
            except OSError:
                LOG.exception("Could not notify systemd that startup completed")
                await self.close()
                return
            self.startup_ready = True

    async def on_disconnect(self) -> None:
        if not self.is_closed():
            LOG.warning("Discord gateway disconnected")

    async def on_resumed(self) -> None:
        LOG.info("Discord gateway session resumed")

    async def command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        cause = getattr(error, "original", error)
        if isinstance(cause, discord.HTTPException) and cause.code in (10062, 40060):
            LOG.warning("Discord interaction %s expired or already had a response", interaction.id)
            return
        if isinstance(cause, app_commands.NoPrivateMessage):
            message = "Puppy Points commands are available in servers."
        elif isinstance(cause, (app_commands.TransformerError, app_commands.CommandSignatureMismatch)):
            message = "Discord sent an invalid command option. Reopen the command and choose a listed value."
            LOG.warning("Invalid slash command input for interaction=%s", interaction.id)
        elif isinstance(cause, sqlite3.Error):
            message = "The Puppy Points database is unavailable. Please try again later."
            LOG.error("Database command failure for interaction=%s", interaction.id, exc_info=cause)
        else:
            message = "The command could not be completed. Please try again later."
            LOG.error("Command failure for interaction=%s", interaction.id, exc_info=cause)
        try:
            if interaction.response.type is discord.InteractionResponseType.deferred_channel_message:
                await interaction.edit_original_response(embed=error_embed(message), allowed_mentions=NO_MENTIONS)
            else:
                await private_error(interaction, message)
        except DISCORD_ERRORS:
            LOG.warning("Could not send command error for interaction=%s", interaction.id)


@app_commands.guild_only()
@app_commands.guild_install()
class PuppyCommands(app_commands.Group, name="puppy", description="Manage and view Puppy Points."):
    def __init__(self, database: Database, role_ids: frozenset[int]) -> None:
        super().__init__()
        self.database = database
        self.role_ids = role_ids

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            raise app_commands.NoPrivateMessage()
        return True

    @app_commands.command(description="Award Puppy Points to a server member.")
    @app_commands.describe(member="Member receiving points", amount="Points to award", reason="Why the points were awarded")
    @app_commands.choices(amount=[app_commands.Choice(name=f"+{value}", value=value) for value in (1, 10, 50, 100)])
    async def add(self, interaction: discord.Interaction, member: discord.Member,
                  amount: app_commands.Choice[int], reason: str | None = None) -> None:
        await self.change_points(interaction, member, amount.value, reason, award=True)

    @app_commands.command(description="Remove Puppy Points from a server member.")
    @app_commands.describe(member="Member losing points", amount="Points to remove", reason="Why the points were removed")
    @app_commands.choices(amount=[app_commands.Choice(name=str(value), value=value) for value in (-1, -10, -50, -100)])
    async def subtract(self, interaction: discord.Interaction, member: discord.Member,
                       amount: app_commands.Choice[int], reason: str | None = None) -> None:
        await self.change_points(interaction, member, amount.value, reason, award=False)

    async def change_points(self, interaction: discord.Interaction, member: discord.Member,
                            amount: int, reason: str | None, *, award: bool) -> None:
        if not await require_staff(interaction, self.role_ids):
            return
        if type(amount) is not int or amount not in VALID_AMOUNTS or (amount > 0) != award:
            await private_error(interaction, "Choose one of the listed transaction amounts.")
            return
        channel = interaction.channel
        permissions = interaction.app_permissions
        can_send = permissions.send_messages_in_threads if isinstance(channel, discord.Thread) else permissions.send_messages
        if not isinstance(channel, discord.abc.Messageable) or not (
            permissions.view_channel and can_send and permissions.embed_links and permissions.attach_files
        ):
            LOG.warning("Missing channel permissions for transaction interaction=%s", interaction.id)
            await private_error(interaction, "Puppy Points needs permission to view this channel, send messages, embed links, and attach files.")
            return

        # A private defer keeps storage errors private while the image posts to the channel.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            attachment = discord.File(ASSETS / IMAGES[amount], filename=IMAGES[amount])
        except OSError:
            LOG.exception("Could not open image asset %s", IMAGES[amount])
            await interaction.edit_original_response(embed=error_embed("The transaction image is unavailable. The balance is unchanged."))
            return
        try:
            transaction, created = await asyncio.to_thread(
                self.database.change_points, interaction.guild_id, member.id, interaction.user.id,
                amount, reason if reason and reason.strip() else None, interaction.id,
            )
            if not created:
                await interaction.edit_original_response(embed=error_embed("This interaction was already recorded."))
                return
            LOG.info(
                "Recorded transaction %s guild=%s member=%s staff=%s change=%+d balance=%d",
                transaction.id, transaction.guild_id, transaction.user_id,
                transaction.staff_id, transaction.amount, transaction.new_balance,
            )
            embed = transaction_embed(transaction)
            try:
                await channel.send(
                    embed=embed, file=attachment,
                    allowed_mentions=NO_MENTIONS, nonce=str(interaction.id),
                )
            except Exception:
                # The balance is already committed, including when a transport failure is ambiguous.
                LOG.exception("Discord could not confirm the public post for transaction %s", transaction.id)
                await interaction.edit_original_response(embed=error_embed(
                    f"Transaction **#{transaction.id}** was saved. The resulting balance is "
                    f"**{point_text(transaction.new_balance)}**. Discord could not confirm the public "
                    "image post. Repeating the command would apply another change."
                ))
                return
        finally:
            attachment.close()
        try:
            await interaction.delete_original_response()
        except DISCORD_ERRORS:
            LOG.warning("Public transaction posted but private acknowledgment could not be dismissed")

    @app_commands.command(description="Show a member's balance and leaderboard rank.")
    @app_commands.describe(member="Member to look up. Leave blank to see your own points.")
    async def points(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        member = member or interaction.user
        await interaction.response.defer(thinking=True)
        balance, rank = await asyncio.to_thread(self.database.points, interaction.guild_id, member.id)
        embed = discord.Embed(title="🐾 Puppy Points Balance", description=member.mention, color=discord.Color.blue())
        embed.add_field(name="Balance", value=point_text(balance))
        embed.add_field(name="Leaderboard rank", value=f"#{rank}" if rank is not None else "Unranked")
        await interaction.edit_original_response(embed=embed, allowed_mentions=NO_MENTIONS)

    async def display_name(self, guild: discord.Guild | None, user_id: int) -> str:
        if guild is not None:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await asyncio.wait_for(guild.fetch_member(user_id), timeout=5)
                except discord.NotFound:
                    pass
                except DISCORD_ERRORS as error:
                    LOG.warning("Could not resolve member %s in guild %s (%s)", user_id, guild.id, type(error).__name__)
            if member is not None and (name := " ".join(member.display_name.split())):
                return embed_text(name, 100)
        return f"User {user_id}"

    async def leaderboard_embed(self, interaction: discord.Interaction, page: int) -> tuple[discord.Embed, int]:
        rows, count = await asyncio.to_thread(self.database.leaderboard, interaction.guild_id, page)
        names = await asyncio.gather(*(self.display_name(interaction.guild, user_id) for user_id, _ in rows))
        medals = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}
        ranks = (
            f"{medals.get(rank, '')}**#{rank}** {name} · {point_text(balance)}"
            for rank, (name, (_, balance)) in enumerate(zip(names, rows), start=page * PAGE_SIZE + 1)
        )
        embed = discord.Embed(
            title="🏆 Puppy Points Leaderboard",
            description="Who is the goodest puppy in the server?\n\n" +
            ("\n".join(ranks) if rows else "No Puppy Points transactions yet."),
            color=discord.Color.gold(),
        )
        pages = page_count(count)
        embed.set_footer(text=f"Page {page + 1} of {pages}")
        return embed, pages

    @app_commands.command(description="Show the server's Puppy Points leaderboard.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        embed, pages = await self.leaderboard_embed(interaction, 0)
        view = Pages(interaction, self.leaderboard_embed, pages)
        await interaction.edit_original_response(embed=embed, view=view, allowed_mentions=NO_MENTIONS)

    @app_commands.command(description="Show a member's Puppy Points transaction history (staff only).")
    @app_commands.describe(member="Member whose transactions to review")
    async def history(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await require_staff(interaction, self.role_ids):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def render(context: discord.Interaction, page: int) -> tuple[discord.Embed, int]:
            rows, count = await asyncio.to_thread(self.database.history, context.guild_id, member.id, page)
            description = member.mention if rows else f"{member.mention} has no transactions yet."
            embed = discord.Embed(title="🐾 Puppy Points History", description=description, color=discord.Color.blue())
            for row in rows:
                balance = f"{row.previous_balance:,} → {row.new_balance:,}"
                date = row.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                details = f"Balance **{balance}** · By <@{row.staff_id}>\n{date}"
                if row.reason:
                    details += f"\n**Reason** {embed_text(row.reason, 400)}"
                embed.add_field(name=point_text(row.amount, signed=True), value=details, inline=False)
            pages = page_count(count)
            embed.set_footer(text=f"Page {page + 1} of {pages}")
            return embed, pages

        embed, pages = await render(interaction, 0)
        view = Pages(interaction, render, pages, self.role_ids) if pages > 1 else None
        await interaction.edit_original_response(embed=embed, view=view, allowed_mentions=NO_MENTIONS)


async def serve(bot: PuppyBot, token: str) -> bool:
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if os.name != "nt":
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, main_task.cancel)
    try:
        async with bot:
            await bot.start(token)
    except asyncio.CancelledError:
        return True
    finally:
        if os.name != "nt":
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)
    return bot.startup_ready


def main() -> int:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    console = logging.StreamHandler()
    console.setFormatter(SecretFormatter(token))
    logging.basicConfig(level=logging.INFO, handlers=[console])
    LOG.info("Starting Puppy Points")
    try:
        verify_assets()
        if not token:
            raise ValueError("DISCORD_TOKEN is missing")
        roles = staff_roles(os.environ.get("PUPPY_STAFF_ROLE_IDS", ""))
        database = Database()
        database.initialize()
        LOG.info("SQLite database initialized")
        return 0 if asyncio.run(serve(PuppyBot(database, roles), token)) else 1
    except discord.LoginFailure:
        LOG.error("Discord rejected the bot token. Check the configured token.")
    except ValueError as error:
        LOG.error("Startup failed: %s", error)
    except sqlite3.Error:
        LOG.exception("SQLite initialization failed")
    except app_commands.CommandSyncFailure:
        LOG.exception("Discord slash command registration failed")
    except KeyboardInterrupt:
        return 0
    except Exception:
        LOG.exception("Puppy Points stopped after a startup or connection failure")
    finally:
        LOG.info("Puppy Points shut down")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
