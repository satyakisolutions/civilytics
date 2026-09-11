"""One-shot test: send a Telegram notification via the `telenotif` lib.

Setup:
  1. pip install telenotif
  2. Message your bot (t.me/sjneeraj_bot) at least once, e.g. "/start" --
     Telegram bots can't message a user who has never started a chat with
     them.
  3. Get your numeric chat id: open
     https://api.telegram.org/bot<TOKEN>/getUpdates in a browser right
     after step 2 and read "chat":{"id": ...} from the JSON.
  4. export WW_TELEGRAM_BOT_TOKEN=<token>
     export WW_TELEGRAM_CHAT_ID=<your chat id>
  5. python scripts/test_telegram_notify.py

Token/chat id are read from env vars, not hardcoded -- don't commit real
values. Rotate the bot token via @BotFather (/revoke) if it was ever
pasted in plaintext anywhere (chat, logs, etc).
"""
import os
import sys

from telenotif import notif


def main():
    token = os.environ.get("WW_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("WW_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Set WW_TELEGRAM_BOT_TOKEN and WW_TELEGRAM_CHAT_ID first -- see this file's docstring.")
        sys.exit(1)

    notifier = notif(bot_token=token, user_id=chat_id)
    ok = notifier.alert("Hi from Wastewatch")
    print("sent" if ok else "FAILED to send")


if __name__ == "__main__":
    main()
