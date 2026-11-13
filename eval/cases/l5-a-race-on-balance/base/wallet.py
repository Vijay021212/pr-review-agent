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
