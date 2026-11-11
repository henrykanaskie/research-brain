# research brain on your phone

research brain runs on your computer. Your phone (or tablet) opens it over Tailscale, after you pair it once.
On a screen 900px wide or less the app rearranges itself for touch:

- **Library**: search, filter by reading status, topic or your own notes, and triage what's to read
  (Start reading, Later, Not relevant) with one tap.
- **Graph**: pinch and drag; tap a dot to select it, tap it again (or Open) to read it.
- **Add**: save a link, DOI or arXiv id to read later, write a quick note, take a photo of a whiteboard or an
  equation (saved as a note with the photo attached), import a PDF from Files, or start an open question.
- **Review**: spaced recall of your own summaries, with big grade buttons.
- **Ask**: ask your library, Quick or Research.
- Notes open as a sheet; drag its top edge down to close it. Reading text is 17px, and wide equations scroll
  sideways inside themselves.
- **Lite**: PDFs open in the phone's own PDF viewer. Zotero import, exports, auto-updating `.bib` files and the
  advanced Library settings say "Available on your computer". Private notes keep their lock and are never sent to
  an AI agent, from any device.

Installed to the home screen it behaves like an app, and appears in the share sheet: share a page from Safari,
Chrome or any app and it opens on Add with the link filled in.

## 1. Put the computer and the phone on Tailscale

1. Install Tailscale on the Mac (`brew install --cask tailscale` or the App Store) and on the phone, and sign in
   to the same account on both.
2. Find the Mac's Tailscale address (`tailscale ip -4`, like `100.101.102.103`) and its MagicDNS name (like
   `my-mac.tail1234.ts.net`) in the Tailscale menu.

Tailscale is the recommended way: only your own devices can connect and the traffic is encrypted (WireGuard). A
plain home network (`RB_HOST=192.168.1.20`) also works, but only on a network you trust: over plain `http://` the
device cookie travels unencrypted on that Wi-Fi. Never use `RB_HOST=0.0.0.0` on a laptop that joins other networks.

## 2. Make the app reachable: https with `tailscale serve` (recommended)

Leave research brain on `127.0.0.1` (the default) and let Tailscale put https in front of it:

```bash
RB_ALLOWED_HOSTS=my-mac.tail1234.ts.net ./run.sh     # your MagicDNS name
tailscale serve --bg 8765                              # https://my-mac.tail1234.ts.net -> 127.0.0.1:8765
```

(Enable HTTPS certificates for your tailnet once, in the Tailscale admin console under DNS.) The phone then opens
`https://my-mac.tail1234.ts.net`. https is what lets the phone install the app and use the share sheet: browsers
only allow service workers and share targets on secure pages. Requests that come through `tailscale serve` carry
proxy headers and the ts.net name, so research brain treats them as another device and requires pairing, even
though they arrive from 127.0.0.1. `tailscale serve reset` turns it off.

### Or: bind to the Tailscale address (plain http)

```bash
RB_HOST=100.101.102.103 ./run.sh      # your Mac's Tailscale IP from step 1
```

Binding to the Tailscale IP (not `0.0.0.0`) means the app is reachable over Tailscale and from nowhere else.
While it's bound like this, `http://127.0.0.1:8765` doesn't answer; `run.sh` opens the Tailscale URL instead (a
browser on the Mac opening the Mac's own address still counts as this computer, no pairing needed). Over
`http://` the phone can use the app in the browser, but can't install it or use the share sheet. The Chrome
extension expects `127.0.0.1`, so it won't connect in this mode. If you reach the Mac by name instead, also set
`RB_ALLOWED_HOSTS=my-mac.tail1234.ts.net`; otherwise the phone gets "wrong Host header" (HTTP 421).

If you use the login agent (`scripts/install_launchd.sh`), add `RB_HOST` or `RB_ALLOWED_HOSTS` to
`~/Library/LaunchAgents/com.research_brain.server.plist` under `EnvironmentVariables`, then run
`launchctl kickstart -k gui/$UID/com.research_brain.server`.

## 3. Pair the phone

1. On the computer: View, Library settings, **Phones and tablets**, **Pair a phone**. A QR code appears (made on
   the page; nothing is sent anywhere). If you have more than one address, pick the one the phone uses.
2. On the phone: scan it with the camera, name the device and tap **Pair this phone**.
3. To install: Safari, Share, **Add to Home Screen** (iPhone); Chrome, menu, **Install app** (Android).

The code works once and expires after 10 minutes. The phone gets a long random token in a cookie that pages
can't read; the computer keeps only its hash, in your per-user settings (not in the library). Paired devices are
listed with when each was last used; **Revoke** locks one out at once. On the phone, Library settings has
**Unpair this device**.

Until a device is paired, every request from it gets "Pair this device from your computer" (HTTP 401): no page,
no notes, no PDFs. The computer itself never needs pairing. A paired phone can read, search, review, ask and add,
but can't change the computer: pairing, devices, auto-updating `.bib` files, library import/export, profiles,
the AI agent choice and other settings answer "Available on your computer" (HTTP 403).

The installed app caches only its own code (pages, scripts, fonts, icons). Notes, PDFs and answers always come
from your computer and are never stored on the phone. If the computer is asleep or off Tailscale, the app opens
and says your computer isn't answering.

## 4. Optional: clip selected text with an iOS Shortcut

Select text in Safari, a PDF, or any app, tap **Share → Clip to research_brain**, and it lands in your library as
a fragment. It's the same `POST /api/clip` the Chrome extension uses: arXiv and DOI pages attach to (or import)
the paper, and anything else becomes a fragment under a source note for that page.

A Shortcut can't keep the app's cookie, so it uses a token instead: on the computer, Library settings, Phones and
tablets, **Token for the iOS Shortcut**. Copy the `Bearer …` value it shows (only once) into the Shortcut below.
It's listed as "iOS Shortcut" and can be revoked like a phone.

Check it from the phone: open `https://my-mac.tail1234.ts.net/api/status` in Safari after pairing. You should see JSON.

### Build the Shortcut

In the **Shortcuts** app on the iPhone: **+** (new shortcut), name it **Clip to research_brain**.

1. Tap the **ⓘ** (details) button, turn on **Show in Share Sheet**, and set **Share Sheet Types** to
   **Text**, **Safari web pages** and **URLs**. Close details.
2. The first block now reads *Receive **Text, Safari web pages and URLs** input from **Share Sheet***.
   Set *If there's no input* to **Ask For** **Text**.
3. Add **Get URLs from Input** (input: *Shortcut Input*).
4. Add **Get Item from List**: *First Item* from *URLs*. Long-press the result and rename the variable
   to **PageURL**.
5. Add **Get Name** of *Shortcut Input*. Rename to **PageTitle**. (For Safari pages this is the page
   title; for plain text it's the text itself, which is fine.)
6. Add **Get Text from Input** (input: *Shortcut Input*). Rename to **Quote**. In Safari, select text
   before tapping Share and the selection comes through here.
7. Add **Ask for Input**: *Text*, prompt **Your note (optional)**, default answer empty. Rename to
   **MyNote**.
8. Add **Get Contents of URL**:
   - URL: `https://my-mac.tail1234.ts.net/api/clip` (with `tailscale serve`), or `http://100.101.102.103:8765/api/clip`
   - Method: **POST**
   - Headers: `Content-Type` = `application/json` and `Authorization` = `Bearer …` (the token from Library settings)
   - Request Body: **JSON**, with these fields (all **Text**):

     | Key | Value |
     | --- | --- |
     | `quote` | *Quote* |
     | `url` | *PageURL* |
     | `page_title` | *PageTitle* |
     | `note` | *MyNote* |
     | `locator` | *(leave empty, or type e.g. `Sec. 3`)* |

9. Add **Get Dictionary Value**: *Value* for key `parent` in *Contents of URL*, then another
   **Get Dictionary Value**: *Value* for key `title` in that *Dictionary Value*. Finish with
   **Show Notification**: `Saved to <Dictionary Value>`.

The body the Shortcut sends:

```json
{
  "quote": "The Bohm criterion requires ions to enter the sheath at the sound speed.",
  "url": "https://arxiv.org/abs/2301.01234",
  "page_title": "Kinetic sheath models",
  "note": "Check the Mach number assumption against our probe data",
  "locator": ""
}
```

`target_id` (optional, a note id) files the clip under a specific note instead of matching by URL. At
least one of `quote` or `note` must be non-empty. The reply is
`{"fragment": {...}, "parent": {...}, "created_parent": true|false}` with status 201. Errors come back
as `{"detail": "..."}`: 400 means nothing to save, 403 means the request carried a foreign `Origin`.

Test it on the Mac first with curl (this computer needs no token; from elsewhere add `-H 'Authorization: Bearer …'`):

```bash
curl -s -X POST http://127.0.0.1:8765/api/clip \
  -H 'Content-Type: application/json' \
  -d '{"quote":"test clip from curl","url":"","page_title":"Phone inbox","note":""}'
```

## What happens to a clip

| You shared | It becomes |
| --- | --- |
| Text selected on an arXiv or doi.org page | A fragment under that paper. If the paper isn't in the library yet, it's imported from Semantic Scholar first (needs internet on the Mac). |
| Text from a page whose URL matches a paper's `url` | A fragment under that paper. |
| Text from any other page | A fragment under a note titled with the page title (one note per page). |
| Text with no URL (Notes, Books, a PDF) | A fragment under a note titled with `page_title`. Set `page_title` to a fixed value like `Phone inbox` in step 8 if you'd rather collect these in one place. |

## Troubleshooting

- **"Could not connect to the server"**: Tailscale is off on one of the devices, the Mac is asleep, or
  the app isn't reachable (`RB_HOST`, or `tailscale serve`). Check `/api/status` in Safari.
- **401 "Pair this device from your computer"**: pair the phone (step 3), or for the Shortcut add the
  `Authorization: Bearer …` header. A revoked device must be paired again.
- **The share sheet doesn't list research brain**: install the app from an `https://` address (`tailscale serve`);
  Android Chrome adds it to the share sheet after install. On iPhone, the share sheet only offers Shortcuts and
  installed native apps, so use the Shortcut above, or copy the link and paste it on Add.
- **403 "Origin ... not allowed"**: something added an `Origin` header (some third-party HTTP apps do).
  Use the built-in Get Contents of URL action.
- **400 "Nothing to save"**: neither text nor a note came through. In Safari, select text before you
  tap Share, or type a note at the prompt.
