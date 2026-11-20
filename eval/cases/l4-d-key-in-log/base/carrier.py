import logging
import os

import httpx

log = logging.getLogger(__name__)


def track(parcel_id: str) -> dict:
    api_key = os.environ["CARRIER_API_KEY"]
    url = f"https://api.carrier.example/parcels/{parcel_id}"
    return httpx.get(url, headers={"X-Api-Key": api_key}).json()
