"""Main menu flow for the Holocron terminal interface."""

from __future__ import annotations

import curses
from types import ModuleType
from typing import TYPE_CHECKING

from ui_common import safe_addstr

if TYPE_CHECKING:
    import holocron


MENU_ITEMS = ("Dashboard", "Docker", "Settings", "Exit")


def draw_menu(screen: "curses._CursesWindow", selected: int) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    title = "HOLOCRON CORE"
    subtitle = "JEDI ARCHIVES CONTROL TERMINAL"
    safe_addstr(screen, 2, max(0, (width - len(title)) // 2), title, curses.color_pair(1) | curses.A_BOLD)
    safe_addstr(screen, 4, max(0, (width - len(subtitle)) // 2), subtitle, curses.color_pair(3))

    menu_width = max(len(item) for item in MENU_ITEMS) + 8
    for index, item in enumerate(MENU_ITEMS):
        text = f"{('>' if index == selected else ' ')}  {item.upper()}".ljust(menu_width)
        attr = curses.color_pair(1)
        if index == selected:
            attr |= curses.A_BOLD | curses.A_REVERSE
        safe_addstr(screen, max(7, (height - len(MENU_ITEMS)) // 2) + index * 2, max(0, (width - menu_width) // 2), text, attr)

    help_text = "↑/↓ Navigate   Enter Select   Q Exit"
    safe_addstr(screen, height - 2, max(0, (width - len(help_text)) // 2), help_text, curses.color_pair(3))
    screen.refresh()


def show_placeholder(screen: "curses._CursesWindow", title: str) -> None:
    screen.nodelay(False)
    screen.erase()
    height, width = screen.getmaxyx()
    for row, text, attr in (
        (max(1, height // 2 - 2), title.upper(), curses.color_pair(1) | curses.A_BOLD),
        (height // 2, "This module is not yet available.", curses.color_pair(3)),
        (height // 2 + 2, "Press any key to return to the main menu.", curses.color_pair(3)),
    ):
        safe_addstr(screen, row, max(0, (width - len(text)) // 2), text, attr)
    screen.refresh()
    screen.getch()
    screen.nodelay(True)
    screen.timeout(100)


def run_menu(screen: "curses._CursesWindow", config: "holocron.Config", services: ModuleType) -> None:
    """Run navigation; import the UI facade lazily to avoid an import cycle."""
    import ui

    ui.configure_curses(screen, 0.1)
    selected = 0
    while True:
        draw_menu(screen, selected)
        key = screen.getch()
        if key == -1:
            continue
        if key in (curses.KEY_UP, ord("k"), ord("K")):
            selected = (selected - 1) % len(MENU_ITEMS)
        elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
            selected = (selected + 1) % len(MENU_ITEMS)
        elif key in (10, 13, curses.KEY_ENTER):
            choice = MENU_ITEMS[selected]
            if choice == "Dashboard":
                ui.HolocronUI(screen, config, services).run()
                ui.configure_curses(screen, 0.1)
            elif choice == "Docker":
                show_placeholder(screen, "Docker Control")
            elif choice == "Settings":
                ui.HolocronUI(screen, config, services, start_in_settings=True).run()
                ui.configure_curses(screen, 0.1)
            else:
                return
        elif key in (ord("q"), ord("Q"), 27):
            return

