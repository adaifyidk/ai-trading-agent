"""Read-only Alpaca paper-account connectivity check. Never submits orders."""
import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient


def main():
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY_ID")
    secret = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not secret or "put_paper" in key or "put_paper" in secret:
        raise SystemExit("Set paper-only ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in .env.")
    client = TradingClient(key, secret, paper=True)
    account = client.get_account()
    print("Connected to Alpaca PAPER account.")
    print("Status:", account.status)
    print("Buying power:", account.buying_power)
    print("Equity:", account.equity)
    print("This script is read-only and does not place orders.")


if __name__ == "__main__":
    main()
