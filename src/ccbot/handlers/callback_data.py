"""Callback data constants for Telegram inline keyboards.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.

Constants:
  - CB_HISTORY_*: History pagination
  - CB_DIR_*: Directory browser navigation
  - CB_WIN_*: Window picker (bind existing unbound window)
  - CB_MACHINE_SELECT: Machine picker selection
  - CB_PERM_NORMAL: Permissions normal mode
  - CB_PERM_DANGEROUS: Permissions skip-permissions mode
  - CB_SCREENSHOT_*: Screenshot refresh
  - CB_ASK_*: Interactive UI navigation (arrows, enter, esc)
  - CB_KEYS_PREFIX: Screenshot control keys (kb:<key_id>:<window>)
"""

# History pagination
CB_HISTORY_PREV = "hp:"  # history page older
CB_HISTORY_NEXT = "hn:"  # history page newer

# Directory browser
CB_DIR_SELECT = "db:sel:"
CB_DIR_UP = "db:up"
CB_DIR_CONFIRM = "db:confirm"
CB_DIR_CANCEL = "db:cancel"
CB_DIR_PAGE = "db:page:"

# Window picker (bind existing unbound window)
CB_WIN_BIND = "wb:sel:"  # wb:sel:<index>
CB_WIN_NEW = "wb:new"  # proceed to directory browser
CB_WIN_CANCEL = "wb:cancel"

# Machine picker
CB_MACHINE_SELECT = "machine_select:"  # machine picker selection (machine_id appended)

# Permissions mode picker
CB_PERM_NORMAL = "perm_normal"  # permissions: normal mode
CB_PERM_DANGEROUS = "perm_dangerous"  # permissions: skip-permissions mode

# Screenshot
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Interactive UI (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"  # aq:up:<window>
CB_ASK_DOWN = "aq:down:"  # aq:down:<window>
CB_ASK_LEFT = "aq:left:"  # aq:left:<window>
CB_ASK_RIGHT = "aq:right:"  # aq:right:<window>
CB_ASK_ESC = "aq:esc:"  # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:"  # aq:enter:<window>
CB_ASK_SPACE = "aq:spc:"  # aq:spc:<window>
CB_ASK_TAB = "aq:tab:"  # aq:tab:<window>
CB_ASK_REFRESH = "aq:ref:"  # aq:ref:<window>

# Screenshot control keys
CB_KEYS_PREFIX = "kb:"  # kb:<key_id>:<window>
