# Linux 平台实现
from .platform_impl import (
    get_config_dir,
    get_startup_command,
    is_startup_enabled,
    set_startup_enabled,
    start_script as platform_start_script,
)

__all__ = [
    "get_config_dir",
    "get_startup_command",
    "is_startup_enabled",
    "set_startup_enabled",
    "platform_start_script",
]
