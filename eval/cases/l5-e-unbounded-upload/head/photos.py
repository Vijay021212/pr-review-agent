from pathlib import Path
from typing import BinaryIO

PHOTOS = Path("/srv/shop/photos")


def save_photo(stream: BinaryIO, product_id: int) -> Path:
    data = stream.read()
    path = PHOTOS / f"{product_id}.jpg"
    path.write_bytes(data)
    return path
