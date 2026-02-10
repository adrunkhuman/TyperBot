"""Permission checking utilities."""

import discord


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
    if not member:
        return False
    admin_roles = {"admin", "typer-admin"}
    return any(role.name.lower() in admin_roles for role in member.roles)
