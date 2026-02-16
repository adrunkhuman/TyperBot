"""Permission checking utilities."""

import discord


def _has_admin_role(member: discord.Member) -> bool:
    """Check if member has an admin role."""
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
    member = interaction.guild.get_member(interaction.user.id)
    return _has_admin_role(member) if member else False


def is_admin_member(member: discord.Member | None) -> bool:
    """Check if member has admin role (for DM workflows)."""
    return _has_admin_role(member) if member else False
