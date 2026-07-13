import requests, os

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app import notify  # shared gate: app/notify.telegram_allowed

secrets = {}
with open(os.path.expanduser('~/.moltrust_secrets')) as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            secrets[k] = v

token = secrets['TELEGRAM_BOT_TOKEN']
chat_id = secrets['TELEGRAM_CHAT_ID']
text = (
    '\U0001f99e HN SUBMIT JETZT\n\n'
    'https://news.ycombinator.com/submitlink?u=https%3A%2F%2Fmoltrust.ch%2Fblog%2Fopenclaw-plugin.html'
    '&t=Show+HN%3A+We+built+a+trust+verification+plugin+for+OpenClaw+(W3C+DID+%2B+reputation+scoring)'
)

# Shared gate: only the real-send path runs in production; elsewhere suppress.
if not notify.telegram_allowed("telegram_hn_remind"):
    print('Status: suppressed (MOLTRUST_ENV != production)')
else:
    r = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        timeout=15,
    )
    # Print only the status. Telegram error bodies can echo the request URL
    # (which contains the bot token in the path) — leaking the body to
    # stdout puts the token into log aggregators / CI scrollback.
    print(f'Status: {r.status_code}')
