import os

# Where the bot forwards every message it sees. This is Zipper's /discord
# endpoint: it decides whether to paste into a live Claude session, wake a
# detached one, or start a new conversation primed with the message.
ZIPPER_URL = os.environ.get('ZIPPER_URL', 'http://127.0.0.1:8800')
ZIPPER_URL = os.environ.get('ZIPPER_URL', ZIPPER_URL)

# Where Zipper (and any agent session) reaches the bot to send, read and attach.
BOT_URL = os.environ.get('BOT_URL', 'http://127.0.0.1:4200')
