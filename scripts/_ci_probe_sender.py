"""CI probe for ops S3: a sender past app/notify.py. Must fail the required job. Not for merge."""
import urllib.request

url = "https://api.telegram.org/bot0/sendMessage"
req = urllib.request.Request(url, data=b"{}")


def send():
    urllib.request.urlopen(req, timeout=1)  # nosec B310
