import random
from typing import Tuple

def play_slot(bet: int) -> Tuple[int, str]:
    """Игра Слот"""
    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
    result = [random.choice(symbols) for _ in range(3)]
    
    # Проверяем выигрышные комбинации
    if result[0] == result[1] == result[2]:
        if result[0] == "7️⃣":
            win = bet * 10
            msg = f"🎰 ДЖЕКПОТ! {result[0]} {result[1]} {result[2]}\nВы выиграли {win} 🪙!"
        elif result[0] == "💎":
            win = bet * 5
            msg = f"🎰 Отлично! {result[0]} {result[1]} {result[2]}\nВы выиграли {win} 🪙!"
        else:
            win = bet * 3
            msg = f"🎰 Хороший раунд! {result[0]} {result[1]} {result[2]}\nВы выиграли {win} 🪙!"
        return win, msg
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        win = bet * 2
        msg = f"🎰 Почти! {result[0]} {result[1]} {result[2]}\nВы выиграли {win} 🪙!"
        return win, msg
    else:
        msg = f"🎰 Увы... {result[0]} {result[1]} {result[2]}\nВы проиграли {bet} 🪙"
        return -bet, msg

def play_dice(bet: int, guess: int) -> Tuple[int, str]:
    """Игра Кубик (угадай число 1-6)"""
    roll = random.randint(1, 6)
    
    if guess == roll:
        win = bet * 6
        msg = f"🎲 Выпало {roll}! Вы угадали! Выигрыш {win} 🪙"
        return win, msg
    else:
        msg = f"🎲 Выпало {roll}. Вы не угадали. Проигрыш {bet} 🪙"
        return -bet, msg

def play_coin(bet: int, choice: str) -> Tuple[int, str]:
    """Игра Монетка (орел/решка)"""
    sides = ["Орел", "Решка"]
    result = random.choice(sides)
    
    if choice == result:
        win = bet * 2
        msg = f"🪙 Выпал {result}! Вы выиграли {win} 🪙"
        return win, msg
    else:
        msg = f"🪙 Выпал {result}. Вы проиграли {bet} 🪙"
        return -bet, msg

def play_dart(bet: int) -> Tuple[int, str]:
    """Игра Дартс (пониженные шансы)"""
    # Увеличиваем разброс очков (0-15 вместо 0-10)
    score = random.randint(0, 15)
    
    if score == 15:
        win = bet * 5
        msg = f"🎯 ЯБЛОЧКО! 15 очков! Выигрыш {win} 🪙"
    elif score >= 12:
        win = bet * 3
        msg = f"🎯 Отличный бросок! {score} очков! Выигрыш {win} 🪙"
    elif score >= 8:
        win = bet * 1
        msg = f"🎯 Хороший бросок! {score} очков! Выигрыш {win} 🪙"
    elif score >= 4:
        win = -bet
        msg = f"🎯 Мало очков... {score}. Проигрыш {bet} 🪙"
    else:
        win = -bet
        msg = f"🎯 Промах! {score} очков. Проигрыш {bet} 🪙"
    
    return win, msg

def play_number(bet: int, guess: int, max_num: int = 10) -> Tuple[int, str]:
    """Игра Угадай число (1-10)"""
    number = random.randint(1, max_num)
    
    if guess == number:
        win = bet * 10
        msg = f"🃏 Число {number}! Вы угадали! Выигрыш {win} 🪙"
    elif abs(guess - number) <= 2:
        win = bet * 2
        msg = f"🃏 Число {number}. Вы были близки! Выигрыш {win} 🪙"
    else:
        win = -bet
        msg = f"🃏 Число {number}. Вы не угадали. Проигрыш {bet} 🪙"
    
    return win, msg