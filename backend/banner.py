import os
import config

# ANSI-цвета
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_banner():
    """Красивый баннер при запуске бота"""
    venv_path = os.environ.get("VIRTUAL_ENV", os.path.join(os.getcwd(), "venv"))
    token_short = config.BOT_TOKEN[:10] + "..." if config.BOT_TOKEN else "не задан"

    banner = f"""
{CYAN}{BOLD}██████╗  █████╗ ██████╗  █████╗ ██████╗ ██╗███████╗███████╗
██╔══██╗██╔══██╗██╔══██╗██╔══██╗██╔══██╗██║██╔════╝██╔════╝
██████╔╝███████║██████╔╝███████║██║  ██║██║███████╗█████╗
██╔═══╝ ██╔══██║██╔══██╗██╔══██║██║  ██║██║╚════██║██╔══╝
██║     ██║  ██║██║  ██║██║  ██║██████╔╝██║███████║███████╗
╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝╚══════╝╚══════╝{RESET}
"""
    print(banner)
    print(f"           {GREEN}ParadiseCoin • виртуальная валюта флуда{RESET}")
    print()
    print(f" {GREEN}●{RESET} Окружение: {CYAN}{venv_path}{RESET}")
    print(f" {GREEN}●{RESET} Токен:     {CYAN}{token_short}{RESET}")
    print()
    print(f" {YELLOW}▶ Запускаю бота...{RESET} (Ctrl+C — стоп, закрытие окна — стоп)")
    print()
