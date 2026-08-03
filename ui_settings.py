"""Settings screen renderer."""

from __future__ import annotations

import curses

from ui_common import SETTINGS_CATEGORIES, safe_addstr


def draw_settings_page(screen: "curses._CursesWindow", selected: int) -> None:
    """Render the settings category list for an existing dashboard instance."""
    height, width = screen.getmaxyx()
    title = "SYSTEM SETTINGS"
    subtitle = "HOLOCRON CONFIGURATION CONSOLE"
    safe_addstr(screen, 1, max(0, (width - len(title)) // 2), title, curses.A_BOLD)
    safe_addstr(screen, 2, max(0, (width - len(subtitle)) // 2), subtitle)
    safe_addstr(screen, 4, 2, "═" * max(1, width - 4))

    for index, category in enumerate(SETTINGS_CATEGORIES):
        row = 7 + index
        if row >= height - 3:
            break
        marker = ">" if index == selected else " "
        attr = curses.A_REVERSE | curses.A_BOLD if index == selected else 0
        safe_addstr(screen, row, 4, f" {marker} {category}", attr, width - 8)

    safe_addstr(screen, height - 2, 2, "↑↓ Navigate   Enter Open   Q Back", curses.A_DIM, width - 4)


def draw_dashboard_settings(
    screen: "curses._CursesWindow",
    settings: list[dict],
    values: dict[str, object],
    selected: int,
) -> None:
    """Render the Dashboard settings screen."""

    height, width = screen.getmaxyx()

    title = "DASHBOARD SETTINGS"
    subtitle = "CONFIGURE DASHBOARD BEHAVIOUR"

    safe_addstr(
        screen,
        1,
        max(0, (width - len(title)) // 2),
        title,
        curses.A_BOLD,
    )

    safe_addstr(
        screen,
        2,
        max(0, (width - len(subtitle)) // 2),
        subtitle,
    )

    safe_addstr(
        screen,
        4,
        2,
        "═" * max(1, width - 4),
    )

    for index, setting in enumerate(settings):
        row = 7 + index

        if row >= height - 3:
            break

        key = setting["key"]
        label = setting["label"]
        value = values[key]

        if setting["type"] == "boolean":
            display_value = "Enabled" if value else "Disabled"
        elif setting["type"] == "choice":
            display_value = str(value).replace("_", " ").title()
        else:
            display_value = f"{value}{setting.get('suffix', '')}"

        marker = ">" if index == selected else " "
        attr = (
            curses.A_REVERSE | curses.A_BOLD
            if index == selected
            else 0
        )

        text = f"{marker} {label:<28} {display_value}"

        safe_addstr(
            screen,
            row,
            4,
            text,
            attr,
            width - 8,
        )

    safe_addstr(
        screen,
        height - 2,
        2,
        "↑↓ Navigate   ←→ Change   Q Back",
        curses.A_DIM,
        width - 4,
    )

