"""Note data shared by the desktop and tests, without legacy auth imports."""

from dataclasses import dataclass, field


@dataclass
class KeepNote:
    id: str
    title: str = ""
    text: str = ""
    html: str = ""
    color_hex: str = "#FFF475"
    pinned: bool = False
    trashed: bool = False
    labels: list = field(default_factory=list)
    sort_key: int = 0
    is_list: bool = False
    list_items: list = field(default_factory=list)
    local_order: int = 0
    dark_mode: bool = False
    archived: bool = False
