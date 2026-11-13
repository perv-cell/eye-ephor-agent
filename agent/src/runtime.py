"""Мелкие сведения о машине, одинаковые для всех коллекторов."""

import platform


def current_os_name() -> str:
    names = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}
    system = platform.system()
    return names.get(system, system.lower() or "unknown")
