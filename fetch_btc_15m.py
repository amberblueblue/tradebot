import csv
import os
import time
from datetime import datetime, timezone

import requests


API_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "币安人生USDT"
INTERVAL = "4h"
TOTAL_LIMIT = 10000
REQUEST_LIMIT = 1000
REQUEST_DELAY = 0.5
OUTPUT_FILE = "data/币安人生_4h.csv"


def fetch_klines_page(end_time=None):
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": REQUEST_LIMIT}
    if end_time is not None:
        params["endTime"] = end_time

    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_klines():
    all_klines = []
    end_time = None

    while len(all_klines) < TOTAL_LIMIT:
        page = fetch_klines_page(end_time=end_time)
        if not page:
            break

        all_klines.extend(page)

        oldest_open_time = page[0][0]
        end_time = oldest_open_time - 1

        if len(page) < REQUEST_LIMIT:
            break

        time.sleep(REQUEST_DELAY)

    unique_klines = {kline[0]: kline for kline in all_klines}
    sorted_klines = sorted(unique_klines.values(), key=lambda kline: kline[0])
    return sorted_klines[-TOTAL_LIMIT:]


def to_iso8601(ms_timestamp):
    dt = datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)
    return dt.isoformat()


def save_to_csv(klines, output_file):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])

        for kline in klines:
            writer.writerow(
                [
                    to_iso8601(kline[0]),
                    kline[1],
                    kline[2],
                    kline[3],
                    kline[4],
                    kline[5],
                ]
            )


def main():
    klines = fetch_klines()
    save_to_csv(klines, OUTPUT_FILE)
    print(f"Saved {len(klines)} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
