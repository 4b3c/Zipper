import os

# Where the bot posts what it receives. The original Zipper service that used to
# answer on 4199 is retired; point this at whatever handles messages now.
ZIPPER_URL = os.environ.get('ZIPPER_URL', 'http://127.0.0.1:4199')

# Where that handler pushes replies back for the bot to deliver.
BOT_URL = os.environ.get('BOT_URL', 'http://127.0.0.1:4200')
