import requests, os

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

r = requests.post(
    f'https://api.telegram.org/bot{token}/sendMessage',
    json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
)
print(f'Status: {r.status_code}, Response: {r.text}')
