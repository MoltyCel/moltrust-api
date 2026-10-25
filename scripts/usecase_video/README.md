# Use-case videos (ops UC-VID)

Four use cases (purchasing, invoices, customer service, A2A), each as a 16:9
video of 60–90 s and a 9:16 Short of at most 30 s, in German and English:
16 files, rendered with HeyGen and uploaded to the playlist "MolTrust Use
Cases" on YouTube.

| File | Does |
|---|---|
| `spec/usecases.json` | Scenes, on-screen labels, narration (de/en), YouTube title, description, tags |
| `spec/heygen.json` | Mode (`no-avatar` or `photo-avatar`), voice per language, avatar id |
| `spec_tools.py` | Checks the limits and estimates each item's length; `python spec_tools.py` |
| `scenes.py` | Draws one PNG per scene with headless Chrome (Mac) |
| `heygen_make.py` | Uploads scene images, renders studio videos on HeyGen v3, downloads MP4s |
| `youtube_auth.py` | One-time Google sign-in by Lars; writes three lines to the server's secrets |
| `youtube_upload.py` | Uploads the MP4s, public, into the playlist; records the URLs |

Narration source: the document "MolTrust – Use Cases Mittelstand". For
invoices and customer service, two sentences each come from the same use
case's "Einsatz von MolTrust" and "Nutzen" paragraphs, so the videos reach 60 s
without long silences.

## Order

On the Mac (Chrome is there):

```
python3 scripts/usecase_video/spec_tools.py            # "spec ok"
python3 scripts/usecase_video/scenes.py --out ~/Downloads/ucvid-work/scenes
scp -r ~/Downloads/ucvid-work moltstack@api.moltrust.ch:Downloads/
```

On the server (`HEYGEN_API_KEY` and the YouTube values are in `~/.moltrust_secrets`):

```
set -a; . ~/.moltrust_secrets; set +a
cd ~/moltstack
venv/bin/python scripts/usecase_video/heygen_make.py plan --item einkauf-short-de   # payload, free
venv/bin/python scripts/usecase_video/heygen_make.py upload                         # scene PNGs -> assets
venv/bin/python scripts/usecase_video/heygen_make.py render --item einkauf-short-de  # one test, costs credits
venv/bin/python scripts/usecase_video/heygen_make.py fetch --wait                   # MP4 + length check
venv/bin/python scripts/usecase_video/heygen_make.py render --all
venv/bin/python scripts/usecase_video/heygen_make.py fetch --wait
venv/bin/python scripts/usecase_video/youtube_upload.py --dry-run
venv/bin/python scripts/usecase_video/youtube_upload.py
```

`render` refuses to run without `--item` or `--all`, skips items already
submitted, and needs every scene image uploaded first. `fetch` reports any
video outside 60–90 s and any Short over 30 s; `youtube_upload.py` uploads only
items that have an MP4 and are not in `results.json` yet.

## YouTube sign-in (Lars, once)

1. Google Cloud Console, signed in as the owner of the MolTrust channel: create
   a project, enable "YouTube Data API v3", set the OAuth consent screen to
   "In production" (in "Testing" the sign-in expires after 7 days), create an
   OAuth client of type "Desktop" and download its JSON.
2. On the Mac:

   ```
   python3 ~/moltstack/ops/work/jobs2/apiclone/scripts/usecase_video/youtube_auth.py ~/Downloads/client_secret_XXXX.json
   ```

   The browser opens; sign in as the channel owner and allow access. The
   script prints `3 values written` and nothing else. No package to install.

## Photo avatar (fallback)

Only if Lars approves the lobster image: create the photo avatar
(`POST /v3/avatars`, `type: photo`), wait for the look to leave `processing`,
put its look id into `spec/heygen.json` as `avatar_id` and set `mode` to
`photo-avatar`. HeyGen documents photo avatars for photos of people; whether
a flat-design lobster passes training and moderation is only known after the
attempt.
