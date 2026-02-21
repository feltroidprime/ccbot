"""Directory browser and window picker UI for session creation.

Provides UIs in Telegram for:
  - Window picker: list unbound tmux windows for quick binding
  - Directory browser: navigate directory hierarchies to create new sessions
  - Machine picker: select which machine to run a session on
  - Permissions picker: choose normal or skip-permissions mode

Key components:
  - DIRS_PER_PAGE: Number of directories shown per page
  - User state keys for tracking browse/picker session
  - build_window_picker: Build unbound window picker UI
  - build_machine_picker: Build machine selection UI
  - build_directory_browser: Build directory browser UI (async)
  - build_permissions_picker: Build permissions mode picker UI
  - clear_window_picker_state: Clear picker state from user_data
  - clear_browse_state: Clear browsing state from user_data
"""

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .callback_data import (
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
    CB_MACHINE_SELECT,
    CB_PERM_DANGEROUS,
    CB_PERM_NORMAL,
    CB_WIN_BIND,
    CB_WIN_CANCEL,
    CB_WIN_NEW,
)
from ..machines import machine_registry

# Directories per page in directory browser
DIRS_PER_PAGE = 6

# User state keys
STATE_KEY = "state"
STATE_BROWSING_DIRECTORY = "browsing_directory"
STATE_SELECTING_WINDOW = "selecting_window"
STATE_SELECTING_MACHINE = "selecting_machine"
BROWSE_PATH_KEY = "browse_path"
BROWSE_PAGE_KEY = "browse_page"
BROWSE_DIRS_KEY = "browse_dirs"  # Cache of subdirs for current path
BROWSE_MACHINE_KEY = "browse_machine"  # Selected machine_id
UNBOUND_WINDOWS_KEY = "unbound_windows"  # Cache of (name, cwd) tuples


def clear_browse_state(user_data: dict | None) -> None:
    """Clear directory browsing state keys from user_data."""
    if user_data is not None:
        user_data.pop(STATE_KEY, None)
        user_data.pop(BROWSE_PATH_KEY, None)
        user_data.pop(BROWSE_PAGE_KEY, None)
        user_data.pop(BROWSE_DIRS_KEY, None)


def clear_window_picker_state(user_data: dict | None) -> None:
    """Clear window picker state keys from user_data."""
    if user_data is not None:
        user_data.pop(STATE_KEY, None)
        user_data.pop(UNBOUND_WINDOWS_KEY, None)


def build_window_picker(
    windows: list[tuple[str, str, str]],
) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    """Build window picker UI for unbound tmux windows.

    Args:
        windows: List of (window_id, window_name, cwd) tuples.

    Returns: (text, keyboard, window_ids) where window_ids is the ordered list for caching.
    """
    window_ids = [wid for wid, _, _ in windows]

    lines = [
        "*Bind to Existing Window*\n",
        "These windows are running but not bound to any topic.",
        "Pick one to attach it here, or start a new session.\n",
    ]
    for _wid, name, cwd in windows:
        display_cwd = cwd.replace(str(Path.home()), "~")
        lines.append(f"• `{name}` — {display_cwd}")

    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(windows), 2):
        row = []
        for j in range(min(2, len(windows) - i)):
            name = windows[i + j][1]
            display = name[:12] + "…" if len(name) > 13 else name
            row.append(
                InlineKeyboardButton(
                    f"🖥 {display}", callback_data=f"{CB_WIN_BIND}{i + j}"
                )
            )
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton("➕ New Session", callback_data=CB_WIN_NEW),
            InlineKeyboardButton("Cancel", callback_data=CB_WIN_CANCEL),
        ]
    )

    text = "\n".join(lines)
    return text, InlineKeyboardMarkup(buttons), window_ids


def build_machine_picker() -> tuple[str, InlineKeyboardMarkup]:
    """Build machine selection keyboard from machine_registry.

    Returns (text, keyboard).
    """
    machines = machine_registry.all()
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(machines), 2):
        row = []
        for m in machines[i : i + 2]:
            label = machine_registry.display_name(m.machine_id)
            row.append(
                InlineKeyboardButton(
                    f"🖥 {label}",
                    callback_data=f"{CB_MACHINE_SELECT}{m.machine_id}",
                )
            )
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Cancel", callback_data=CB_DIR_CANCEL)])
    text = "*Select Machine*\n\nWhich machine should this session run on?"
    return text, InlineKeyboardMarkup(buttons)


async def build_directory_browser(
    current_path: str, machine_id: str = "local", page: int = 0
) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    """Build directory browser UI.

    Returns: (text, keyboard, subdirs) where subdirs is the full list for caching.
    """
    machine = machine_registry.get(machine_id)
    path_str = current_path

    # Get subdirs via machine
    all_subdirs = await machine.list_dir(path_str)

    total_pages = max(1, (len(all_subdirs) + DIRS_PER_PAGE - 1) // DIRS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * DIRS_PER_PAGE
    page_dirs = all_subdirs[start : start + DIRS_PER_PAGE]

    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(page_dirs), 2):
        row = []
        for j, name in enumerate(page_dirs[i : i + 2]):
            display = name[:12] + "…" if len(name) > 13 else name
            # Use global index (start + i + j) to avoid long dir names in callback_data
            idx = start + i + j
            row.append(
                InlineKeyboardButton(
                    f"📁 {display}", callback_data=f"{CB_DIR_SELECT}{idx}"
                )
            )
        buttons.append(row)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀", callback_data=f"{CB_DIR_PAGE}{page - 1}")
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton("▶", callback_data=f"{CB_DIR_PAGE}{page + 1}")
            )
        buttons.append(nav)

    # Resolve path for display and "up" detection
    resolved_path = Path(path_str).expanduser().resolve()
    action_row: list[InlineKeyboardButton] = []
    # Allow going up unless at filesystem root
    if resolved_path != resolved_path.parent:
        action_row.append(InlineKeyboardButton("..", callback_data=CB_DIR_UP))
    action_row.append(InlineKeyboardButton("Select", callback_data=CB_DIR_CONFIRM))
    action_row.append(InlineKeyboardButton("Cancel", callback_data=CB_DIR_CANCEL))
    buttons.append(action_row)

    # Display path: replace home dir with ~ for local machine only
    local_id = machine_registry.local_machine_id
    if machine_id in ("local", local_id):
        display_path = str(resolved_path).replace(str(Path.home()), "~")
    else:
        display_path = path_str

    if not all_subdirs:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\n_(No subdirectories)_"
    else:
        text = f"*Select Working Directory*\n\nCurrent: `{display_path}`\n\nTap a folder to enter, or select current directory"

    return text, InlineKeyboardMarkup(buttons), all_subdirs


def build_permissions_picker(
    machine_id: str, work_dir: str
) -> tuple[str, InlineKeyboardMarkup]:
    """Build permissions mode picker after directory selection."""
    display = machine_registry.display_name(machine_id)
    dirname = work_dir.rstrip("/").split("/")[-1] or work_dir
    text = f"*Run mode for [{display}] {dirname}*\n\nNormal mode or skip all permission prompts?"
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Normal", callback_data=CB_PERM_NORMAL),
            InlineKeyboardButton(
                "Skip permissions ⚡", callback_data=CB_PERM_DANGEROUS
            ),
        ]
    ]
    return text, InlineKeyboardMarkup(buttons)
