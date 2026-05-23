"""Permission checking utilities."""

from typing import TYPE_CHECKING, cast

import discord

if TYPE_CHECKING:
    from typer_bot.database import Database

SETUP_REQUIRED_MESSAGE = (
    "TyperBot is not set up for this server. A server admin must run `/admin panel` first."
)


def _interaction_member(interaction: discord.Interaction) -> discord.Member | None:
    if not interaction.guild:
        return None

    cached_member = interaction.guild.get_member(interaction.user.id)
    if cached_member is not None:
        return cached_member

    if isinstance(interaction.user, discord.Member) or hasattr(interaction.user, "roles"):
        return cast(discord.Member, interaction.user)

    return None


def _has_admin_role(member: discord.Member, admin_role_id: str | None = None) -> bool:
    """Check configured admin role, or default admin role names when no config exists."""
    if admin_role_id is not None:
        return any(str(role.id) == admin_role_id for role in member.roles)

    admin_roles = {"admin", "typer-admin"}
    return any(role.name.lower() in admin_roles for role in member.roles)


def is_admin(interaction: discord.Interaction) -> bool:
    """Check if interaction user has admin role on the originating guild.

    Args:
        interaction: The Discord interaction to check.

    Returns:
        True if the user has an admin role on the guild where the interaction
        originated, False otherwise (including if called from DMs).
    """
    if not interaction.guild:
        return False
    member = _interaction_member(interaction)
    return _has_admin_role(member) if member else False


async def is_configured_admin(interaction: discord.Interaction, db: "Database") -> bool:
    """Check whether the user has the configured TyperBot admin role."""
    if not interaction.guild or interaction.guild_id is None:
        return False

    config = await db.guild_config.get_guild_config(str(interaction.guild_id))
    if config is None:
        return False

    member = _interaction_member(interaction)
    return _has_admin_role(member, config["admin_role_id"]) if member else False


async def get_configured_admin_role_mention(guild_id: str, db: "Database") -> str | None:
    """Return the configured TyperBot admin role mention for one guild."""
    config = await db.guild_config.get_guild_config(guild_id)
    if config is None:
        return None
    return f"<@&{config['admin_role_id']}>"


async def get_admin_permission_error(
    interaction: discord.Interaction,
    db: "Database",
) -> str | None:
    """Return the user-visible reason an admin action should be blocked."""
    if not interaction.guild or interaction.guild_id is None:
        return "This command can only be used in a server."

    config = await db.guild_config.get_guild_config(str(interaction.guild_id))
    if config is None:
        return SETUP_REQUIRED_MESSAGE

    member = _interaction_member(interaction)
    if member is None or not _has_admin_role(member, config["admin_role_id"]):
        return "You no longer have permission to use admin commands."

    return None


def is_admin_member(member: discord.Member | None, admin_role_id: str | None = None) -> bool:
    """Check if a guild member currently has an admin role."""
    return _has_admin_role(member, admin_role_id) if member else False


def has_setup_permission(interaction: discord.Interaction) -> bool:
    """Setup requires Discord Administrator or Manage Server, not the TyperBot admin role."""
    if not interaction.guild:
        return False

    member = _interaction_member(interaction)
    if member is None:
        return False

    permissions = getattr(member, "guild_permissions", None)
    return bool(
        permissions
        and (
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
        )
    )


def get_admin_role_mention(guild: discord.Guild | None) -> str | None:
    """Return the preferred admin role mention for a guild, if available."""
    if guild is None or not hasattr(guild, "roles"):
        return None

    preferred_names = ["typer-admin", "admin"]
    lowered = {name: name.lower() for name in preferred_names}
    for preferred_name in preferred_names:
        for role in guild.roles:
            if role.name.lower() == lowered[preferred_name]:
                return f"<@&{role.id}>"
    return None
