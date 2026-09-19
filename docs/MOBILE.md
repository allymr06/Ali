# Mobile companion (Android PWA)

The phone reaches the JARVIS that runs on your Windows PC. Nothing runs on
the phone itself: the PC must be **awake, online, and running JARVIS**, or
the app shows "Bilgisayara ulaşılamıyor" and nothing else. Same core, same
conversations, same tasks, same permission approvals as the desktop window.

## How it fits together

```
Android browser / installed PWA
        │  HTTPS over the Tailscale private network (WireGuard)
        ▼
Tailscale Serve on the PC  (https://<pc>.<tailnet>.ts.net)
        │  plain HTTP, loopback only
        ▼
JARVIS desktop process  →  app/mobile server on 127.0.0.1:8765
        │
        └─ the same DesktopController / core engine / permission engine
```

Two separate protections, both required:

- **Network**: only devices in your tailnet can reach the PC. No Funnel, no
  port forwarding, nothing on the public internet.
- **Application**: every request needs a paired device session (an
  HttpOnly, SameSite=Strict cookie). Mutations also need the page's own
  header and a matching origin. Model credentials never leave the PC.

Environment (see `docs/CONFIGURATION.md`): `JARVIS_MOBILE_ENABLED` (default
on), `JARVIS_MOBILE_PORT` (8765), `JARVIS_MOBILE_SESSION_DAYS` (30),
`JARVIS_MOBILE_PAIRING_TTL_SECONDS` (600).

## 1. On the PC: start JARVIS

Start the desktop application as usual (the shortcut, or
`python -m app.ui`). The mobile server starts inside it and listens on
`127.0.0.1:8765` only. Ayarlar › Telefon shows "127.0.0.1:8765 · N cihaz"
when it is up.

## 2. On the PC: Tailscale Serve

Checked against the Tailscale documentation on 17 September 2026
(kb/1242 *Tailscale Serve*, kb/1153 *Enabling HTTPS*). Tailscale is not
installed on the development PC, so these steps are documented, not
executed; do them once on your PC.

1. Install Tailscale for Windows (tailscale.com/download) and log in from
   the tray icon. Install the Android app and log in to the **same**
   account.
2. In the admin console → **DNS**, enable **MagicDNS** and **HTTPS
   Certificates**. Serve provisions the certificate itself.
3. In a terminal on the PC (the installer puts `tailscale` on PATH; if not,
   use `"C:\Program Files\Tailscale\tailscale.exe"`):

   ```powershell
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   tailscale serve status
   ```

   `--bg` keeps the serve configuration across reboots. To stop:
   `tailscale serve --https=443 off`, or `tailscale serve reset` to clear
   everything.
4. Note the address from `tailscale serve status`:
   `https://<pc-name>.<tailnet>.ts.net`. That is the only address the phone
   uses. Do **not** use `tailscale funnel`; the app is for you alone.

If you changed `JARVIS_MOBILE_PORT`, use that port in the serve command.

## 3. Pair the phone

1. On the PC: Ayarlar › Telefon → **Eşleştirme kodu üret**, or in a terminal:

   ```powershell
   python scripts/mobile_pair.py code
   ```

   The code (`ABCD-EFGH`) is valid for 10 minutes, single-use, and shown
   once. Only the newest code is live.
2. On the phone (Wi-Fi or mobile data, Tailscale connected): open the
   `https://…ts.net` address, type the code and a device name, tap
   **Bağlan**. The session lasts 30 days.
3. To revoke a phone: Ayarlar › Telefon → **Çıkar** (or **Tümünü çıkar**),
   or `python scripts/mobile_pair.py revoke <id|all>`. A revoked phone loses
   access on its next request and its live channel closes. **Bu cihazın
   oturumunu kapat** in the phone's Ayarlar tab does the same for that phone.

## 4. Install as an app (Android)

Chrome or Edge on Android: open the address, menu ⋮ → **Uygulamayı yükle**
(or the install banner / "Ana ekrana ekle"). It launches standalone with
the JARVIS icon. The installed shell opens offline and says so plainly; an
offline shell is not an offline assistant.

## The whole desktop page on the phone

After pairing, the phone is sent to `https://…ts.net/nova/`: the desktop
Nova page itself, served by the same loopback server with a small shim
in front of it (`app/mobile/web/nova-shim.js`). Every `window.pywebview.api`
call becomes an authenticated `POST /api/bridge/<method>` answered by the
real `NovaBridge`, and every push the desktop window receives is mirrored
over the event channel - so the Medical Academy (plan, subjects, library,
notes, exams, question bank, understanding, histology, cards, professor
style, progress, Anatomy Lab), tasks, memory, research, routines and the
rest work on the phone exactly as on the PC, on the same records. A
phone layer (`css/phone.css`, active only under `body.phone`) turns the
rail into a bottom bar, stacks every layout, grows touch targets and
gives the WebGL lab a fixed viewport: one finger turns the model, two
fingers pinch to zoom and drag to pan, the on-screen arrows still work.

Stays on the PC, refused in words from the phone: API key and model
settings, pairing and device management, native file and folder
dialogs (imports and exports), the PC screen, window controls (compact
mode). The light client remains at `/?lite=1`.

### Voice on the phone

The desktop's voice session uses the PC's microphone and speakers, so it
cannot be proxied. On the phone the loop runs in the browser instead:
tap the microphone (or "Sesli mod"), allow the microphone once, and the
page records until you pause; the recording goes to the PC as 16 kHz WAV,
the PC's own speech recognizer turns it into text (`POST
/api/voice/transcribe`), the transcript enters the desktop's own
`submit_command` marked as spoken - same conversation, same permission
pipeline, the desktop chat shows it too - and the reply is synthesized
by the PC's own voice (`POST /api/voice/speak`, the local Windows voice
as fallback, exactly like the desktop) and played on the phone. Then it
listens again, until you close the stage or twelve seconds of silence
pass twice. The phone's audio never touches the PC's devices.

It needs the HTTPS address (browsers give microphone access only on a
secure origin) and a foreground tab: Android suspends audio in the
background, and the session says so and ends rather than pretending to
listen.

## What the phone can do in this release

- **Sohbet**: text chat with streamed replies, switch or start
  conversations - the same records the desktop lists.
- **Görevler**: existing tasks with status and steps; pause / resume /
  cancel only when the task's state allows it; permission approvals for
  the exact pending action (a reconnect never approves anything).
- **Ayarlar**: connection details, this device's session, logout.

Not in this release, by design: voice, wake words, push notifications,
camera, file uploads, the Medical Academy screens, a native APK.

## Honest states

- "yeniden bağlanıyor" - the PC is out of reach; the app keeps trying.
- "Bağlantı koptu; sonuç bilinmiyor" - a message left the phone while the
  connection dropped. After reconnecting the app asks the PC what
  happened: if the PC never received it, it says so and offers **Yeniden
  gönder**; it never resends on its own.
- "JARVIS masaüstünde duraklatıldı" - the desktop pause applies to the
  phone too.

## Troubleshooting

- `tailscale serve status` shows nothing → run the serve command again
  (with `--bg`).
- Certificate warnings → check MagicDNS and HTTPS Certificates in the admin
  console, then `tailscale serve reset` and re-add.
- "Eşleştirme kodunun süresi doldu" → mint a new code; six wrong attempts
  in a minute are refused for a minute.
- The phone connects only on Wi-Fi → make sure the Tailscale Android app
  is connected (VPN key icon) on mobile data as well.
