import threading
from dataclasses import dataclass

# Wallets are changed from the web server's worker threads.
LOCK = threading.Lock()


@dataclass
class Wallet:
    balance: float


def deposit(wallet: Wallet, amount: float) -> None:
    with LOCK:
        wallet.balance += amount


def withdraw(wallet: Wallet, amount: float) -> bool:
    if wallet.balance >= amount:
        wallet.balance -= amount
        return True
    return False
