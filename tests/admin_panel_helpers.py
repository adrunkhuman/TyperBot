"""Shared helpers for admin panel interaction tests."""

import discord


def get_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    return next(child for child in view.children if getattr(child, "label", None) == label)


def has_button(view: discord.ui.View, label: str) -> bool:
    return any(getattr(child, "label", None) == label for child in view.children)


def selected_option_labels(select: discord.ui.Select) -> list[str]:
    return [option.label for option in select.options if option.default]


def option_values(select: discord.ui.Select) -> set[str]:
    return {option.value for option in select.options}
