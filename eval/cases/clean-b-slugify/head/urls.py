import re
import unicodedata

BASE_URL = "https://shop.example"


def slugify(name: str) -> str:
    """'Red Café Mug!' -> 'red-cafe-mug'."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_name.lower())
    return "-".join(words)


def product_url(name: str) -> str:
    return f"{BASE_URL}/products/{slugify(name)}"
