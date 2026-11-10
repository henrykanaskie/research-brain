# Capturing from your iPhone (iOS Shortcut over Tailscale)

Select text in Safari, a PDF, or any app, tap **Share → Clip to research_brain**, and it lands in your
library as a fragment. It's the same `POST /api/clip` the Chrome extension uses: arXiv and DOI pages
attach to (or import) the paper, and anything else becomes a fragment under a source note for that page.

This needs two things the app doesn't do by default: the server has to listen on more than this Mac
(`RB_HOST`), and your phone has to reach the Mac privately (Tailscale).

## 1. Put the Mac and the phone on Tailscale

1. Install Tailscale on the Mac (`brew install --cask tailscale` or the App Store) and on the iPhone,
   and sign in to the same account on both.
2. On the Mac, find its Tailscale address: `tailscale ip -4` (something like `100.101.102.103`), or its
   MagicDNS name (like `my-macbook.tail1234.ts.net`) from the Tailscale menu.

## 2. Start research_brain bound to the Tailscale address

```bash
RB_HOST=100.101.102.103 ./run.sh      # your Mac's Tailscale IP from step 1
```

Binding to the Tailscale IP (not `0.0.0.0`) means the app is reachable over Tailscale and from nowhere
else: not from the café Wi-Fi, not from your LAN. While it's bound like this, `http://127.0.0.1:8765`
doesn't answer; `run.sh` opens the Tailscale URL instead, and the origin guard accepts pages served
from that address. The Chrome extension expects `127.0.0.1`, so it won't connect in this mode.

If you use the login agent (`scripts/install_launchd.sh`), add `RB_HOST` to
`~/Library/LaunchAgents/com.research_brain.server.plist` under `EnvironmentVariables`, then run
`launchctl kickstart -k gui/$UID/com.research_brain.server`.

Check it from the phone: open `http://100.101.102.103:8765/api/status` in Safari. You should see JSON.

The server only answers requests addressed to `127.0.0.1`, `localhost` or the `RB_HOST` address (this
stops DNS-rebinding attacks; see SECURITY.md). If you reach the Mac by a name instead, such as its
Tailscale MagicDNS name, list it too: `RB_ALLOWED_HOSTS=my-mac.tailnet-name.ts.net`. Otherwise the phone
gets "wrong Host header" (HTTP 421).

### The security tradeoff (read this)

The server has **no login**. On `127.0.0.1` that's fine, because only programs on your Mac can reach it,
and the origin guard stops websites in your browser from calling it. With `RB_HOST` set, **anyone who
can reach that address and port can read, change and delete your notes and PDFs, and run Claude
requests on your subscription.**

- Safe enough: `RB_HOST=<your Tailscale IP>`. Only devices signed in to your tailnet can connect. If
  you share your tailnet with other people, use Tailscale ACLs to limit port 8765 to your own devices.
- Avoid: `RB_HOST=0.0.0.0` on a laptop. That exposes the app on every network you join. Only do it on a
  network you fully control, and prefer the Tailscale IP anyway.
- Traffic over Tailscale is encrypted end to end (WireGuard), even though the URL says `http://`.
- `run.sh` prints a warning whenever `RB_HOST` isn't loopback. Go back to the default by starting it
  without `RB_HOST`.

The origin guard rejects requests that carry a foreign `Origin` header (that's how browsers label
cross-site requests). **iOS Shortcuts don't send an `Origin` header at all**, so their requests are
allowed. That's what makes the Shortcut work, and it's also why the network restriction above is what
actually protects you. Don't open the app's URL in a phone browser tab you also use for other sites
and expect the guard to cover more than it does.

## 3. Build the Shortcut

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
   - URL: `http://100.101.102.103:8765/api/clip` (your Tailscale IP or MagicDNS name)
   - Method: **POST**
   - Headers: `Content-Type` = `application/json`
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

Test it from the Mac first with curl (no `Origin` header, same as the Shortcut):

```bash
curl -s -X POST http://100.101.102.103:8765/api/clip \
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
  the app isn't running with `RB_HOST`. Check `http://<ip>:8765/api/status` in Safari.
- **403 "Origin ... not allowed"**: something added an `Origin` header (some third-party HTTP apps do).
  Use the built-in Get Contents of URL action.
- **400 "Nothing to save"**: neither text nor a note came through. In Safari, select text before you
  tap Share, or type a note at the prompt.
