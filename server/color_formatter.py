# server/color_formatter.py

import logging
from colorama import Fore, Style, init

init(autoreset=True)  # resets colors after each line

class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG": Fore.BLUE,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.MAGENTA
    }

    def format(self, record):
        level_color = self.LEVEL_COLORS.get(record.levelname, "")
        levelname = f"{level_color}[{record.levelname}]{Style.RESET_ALL}"
        time = f"[{self.formatTime(record, '%Y-%m-%d %H:%M:%S')}]"
        name = f"[{record.name}]"
        message = record.getMessage()
        return f"{time} {levelname} {name} {message}"
