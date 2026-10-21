import random
from typing import Tuple


def _win_message(label: str, amount: int) -> str:
    return f"{label}\nЧистый выигрыш: {amount:,} 🪙" if amount > 0 else f"{label}\nСтавка возвращена."


def play_slot(bet: int) -> Tuple[int, str]:
    """Casino slots; returns the net balance change (same rules as Mini App)."""
    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
    result = [random.choice(symbols) for _ in range(3)]
    line = f"🎰 {result[0]} {result[1]} {result[2]}"

    if result[0] == result[1] == result[2]:
        payout = bet * 2
        net = payout - bet
        return net, (
            f"{line}\n🎉 Три одинаковых символа!\n"
            f"Выплата: {payout:,} LDC\nЧистая прибыль: {net:,} LDC"
        )

    if result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        payout = bet // 2
        net = payout - bet
        return net, (
            f"{line}\n🟡 Два символа совпали.\n"
            f"Возврат 50% ставки: {payout:,} LDC\n"
            f"Потеряно: {bet - payout:,} LDC"
        )

    return -bet, f"{line}\n❌ Три разных символа — проигрыш {bet:,} LDC."


def play_dice(bet: int, guess: int) -> Tuple[int, str]:
    """Угадай кубик: x5 валовой выплаты при угадывании (чистая прибыль x4)."""
    roll = random.randint(1, 6)
    if guess == roll:
        net = bet * 4
        return net, _win_message(f"🎲 Выпало {roll}! Ты угадал!", net)
    return -bet, f"🎲 Выпало {roll}. Не угадал — проигрыш {bet:,} 🪙."


def play_coin(bet: int, choice: str) -> Tuple[int, str]:
    """Монетка: x2 валовой выплаты; чистая прибыль равна ставке."""
    result = random.choice(["Орел", "Решка"])
    if choice == result:
        return bet, _win_message(f"🪙 Выпал {result}!", bet)
    return -bet, f"🪙 Выпал {result}. Проигрыш {bet:,} 🪙."


def play_dart(bet: int) -> Tuple[int, str]:
    """Дартс с небольшим математическим преимуществом казино."""
    score = random.randint(0, 15)
    if score == 15:
        net = bet * 4
        return net, _win_message(f"🎯 ЯБЛОЧКО! {score} очков!", net)
    if score >= 12:
        return bet, _win_message(f"🎯 Отличный бросок! {score} очков!", bet)
    if score >= 8:
        return 0, f"🎯 {score} очков. Ставка {bet:,} 🪙 возвращена."
    return -bet, f"🎯 {score} очков. Проигрыш {bet:,} 🪙."


def play_number(bet: int, guess: int, max_num: int = 10) -> Tuple[int, str]:
    """Угадай число: точное попадание x5 валовой выплаты, близкое — возврат ставки."""
    number = random.randint(1, max_num)
    if guess == number:
        net = bet * 4
        return net, _win_message(f"🃏 Число {number}! Точное попадание!", net)
    if abs(guess - number) <= 2:
        return 0, f"🃏 Число {number}. Было близко — ставка {bet:,} 🪙 возвращена."
    return -bet, f"🃏 Число {number}. Не угадал — проигрыш {bet:,} 🪙."
