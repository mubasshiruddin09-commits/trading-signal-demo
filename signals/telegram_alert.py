"""
Layer 4 - Signal & Alert Service (Product B)
Read-only signal generation. No execution here, per the spec.

SETUP (do this yourself, takes ~5 min):
1. Open Telegram, message @BotFather
2. Send /newbot, follow prompts, get your BOT_TOKEN
3. Message your new bot once (so it can message you back)
4. Get your CHAT_ID by visiting:
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   (after messaging the bot) - look for "chat":{"id": ...}
5. Put both values in a .env file (see .env.example)
"""
import os
import requests


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_alert(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[DRY RUN - no bot token/chat id set] Would send:")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    return resp.status_code == 200


def format_signal_message(symbol: str, timeframe: str, score: int, breakdown: dict) -> str:
    lines = [
        f"📊 Signal: {symbol} ({timeframe})",
        f"Confluence score: {score}",
        "Components:",
    ]
    for k, v in breakdown.items():
        lines.append(f"  • {k}: {v}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Smoke test - dry run since no token set here
    msg = format_signal_message("BTCUSDT", "1D", 2, {"rsi_oversold": True, "at_swing_low": True})
    send_alert(msg)
