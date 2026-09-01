---
name: hermes-channel-connect
description: Connect Hermes to WeChat, Feishu, or DingTalk via QR.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hermes, gateway, weixin, wechat, feishu, lark, dingtalk, messaging, qr-login, onboarding]
    related_skills: [hermes-agent]
    requires_toolsets: [terminal]
---

# Connect Hermes to a Messaging Channel Skill

Connect a Hermes agent to a messaging channel via QR-code onboarding, in the
shortest reliable path. Covers three channels: **WeChat / Weixin (微信)** via
Tencent's iLink Bot API, **Feishu / Lark (飞书 / 国际版 Lark)** via scan-to-create
bot onboarding, and **DingTalk (钉钉)** via device-flow QR authorization (Stream
Mode). All flows skip the interactive `hermes gateway setup` wizard (which blocks
in a non-TTY agent shell) and drive the QR flow directly, then restart the gateway
safely. Does **not** cover WeCom (企业微信), Telegram, Discord, or the manual
"bring your own credentials" paths.

## When to Use

- User says "接入微信 / 连接飞书 / 连接钉钉 / connect WeChat / Feishu / DingTalk" on a Hermes install.
- User wants a new Hermes agent (or cloned profile) hooked to a channel.
- User wants to switch an existing channel's access policy (open / pairing / allowlist).

Don't use for: WeCom, Telegram, Discord, Slack, WhatsApp, or any non-Hermes
integration. Those have their own platform adapters under `gateway/platforms/`
and are not driven by this skill's QR flow.

## Prerequisites

- **Hermes install dir and home.** Confirm via `terminal(command="hermes --version")`
  (install dir, e.g. `/opt/hermes`) and `terminal(command="echo $HERMES_HOME")`
  (or `get_hermes_home()`, e.g. `~/.hermes`).
- **Weixin:** `aiohttp` + `cryptography` in the hermes venv; reachability to
  `ilinkai.weixin.qq.com:443`.
- **Feishu:** reachability to `accounts.feishu.cn:443` (or `accounts.larksuite.com:443`);
  `qrcode` (optional, ASCII QR only). `lark_oapi` is NOT required for the QR
  registration itself — the gateway lazy-installs it on first connect.
- **DingTalk:** reachability to `oapi.dingtalk.com:443`; `requests` in the venv.
  `dingtalk-stream` + `httpx` are NOT required for the QR authorization — the
  gateway lazy-installs them on first connect (Stream Mode).
- **A phone** with the target app ready to scan and confirm (no fully-automatic path).

### Access Policy (pairing vs no-pairing)

All channels gate who can DM the bot. Three modes:

| Mode | Weixin env | Feishu env | DingTalk env | Meaning |
|---|---|---|---|---|
| **no-pairing (default)** | `WEIXIN_DM_POLICY=open` + `WEIXIN_ALLOW_ALL_USERS=true` | `FEISHU_ALLOW_ALL_USERS=true` | `DINGTALK_ALLOWED_USERS=*` | Anyone can DM immediately after scan |
| **pairing** | `WEIXIN_DM_POLICY=pairing` | `FEISHU_ALLOW_ALL_USERS=false` + `FEISHU_ALLOWED_USERS=""` | `DINGTALK_ALLOWED_USERS=""` | Unknown users must request; approve with `hermes pairing approve` |
| **allowlist** | `WEIXIN_DM_POLICY=allowlist` + `WEIXIN_ALLOWED_USERS=…` | `FEISHU_ALLOW_ALL_USERS=false` + `FEISHU_ALLOWED_USERS=…` | `DINGTALK_ALLOWED_USERS=id1,id2` | Only listed user IDs can DM |

The script defaults to **no-pairing**. Pass `--pairing` for pairing mode, or
`--allow <id1,id2>` for an explicit allowlist.

### `.env` keys written by this skill

```
# --- hermes-channel-connect skill block ---
# Weixin
WEIXIN_ACCOUNT_ID=
WEIXIN_TOKEN=
WEIXIN_BASE_URL=
WEIXIN_DM_POLICY=open          # open | pairing | allowlist
WEIXIN_ALLOW_ALL_USERS=true
WEIXIN_ALLOWED_USERS=
WEIXIN_GROUP_POLICY=disabled
# Feishu
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_DOMAIN=feishu           # feishu | lark
FEISHU_CONNECTION_MODE=websocket
FEISHU_ALLOW_ALL_USERS=true
FEISHU_ALLOWED_USERS=
# DingTalk
DINGTALK_CLIENT_ID=
DINGTALK_CLIENT_SECRET=
DINGTALK_ALLOWED_USERS=*
# --- end hermes-channel-connect skill block ---
```

## How to Run

Invoke the bundled driver through the `terminal` tool, in the **background** so
it prints the scan URL and keeps polling while you relay the URL to the user:

```
terminal(
  command="HERMES_INSTALL=<install_dir> <venv_python> <skill>/scripts/connect_channel.py <channel> [--pairing|--allow ids]",
  background=true
)
```

where `<channel>` is `weixin`, `feishu`, or `dingtalk`. Then poll the background
process, grab the scan URL, and hand it to the user ("用手机扫这个链接并确认").
Prefer the URL over the ASCII QR. On `{"ok": true, ...}`, restart the gateway
safely (see Procedure step 6).

## Quick Reference

```
terminal(command="hermes --version")                              # install dir + version
terminal(command="hermes gateway status")                         # gateway PID / running?
terminal(command="pgrep -f 'hermes gateway run'")                 # gateway PID
terminal(command="ps -o pid,ppid,pgid,cmd -p <agent_shell_pid>")  # which tree is the agent in
terminal(command="s6-svc -r /run/service/gateway-default")       # restart (s6/Docker, Linux)
terminal(command="systemctl restart hermes-gateway-default")     # restart (systemd, Linux)
search_files(pattern="weixin|feishu|dingtalk|platform\\(s\\)", path="<home>/logs/gateway.log")  # confirm connected
```

## Procedure

1. **Confirm deps + network.** Weixin: `import aiohttp, cryptography` + connect
   `ilinkai.weixin.qq.com:443`. Feishu: connect `accounts.feishu.cn:443`.
   DingTalk: connect `oapi.dingtalk.com:443` + `import requests`. The SDK deps
   (`lark_oapi` for Feishu, `dingtalk-stream`+`httpx` for DingTalk) are NOT
   needed for the QR step — they lazy-install at connect time.
2. **Locate paths.** `terminal(command="hermes --version")`;
   `terminal(command="export PATH=\"<install_dir>/bin:$PATH\"")`.
3. **Drive the QR flow.** Run `scripts/connect_channel.py <channel>` in the
   background via `terminal(..., background=true)`. It imports the right module
   per channel and writes `.env`. **Do not** run `hermes gateway setup` from the
   agent shell — interactive, hangs without a TTY. (Feishu/DingTalk adapters
   live under `plugins/platforms/<name>`, NOT `gateway/platforms/` — they are
   bundled plugins.)
4. **Relay the scan URL.** Weixin: the liteapp URL from `qrcode_img_content`.
   Feishu: the `verification_uri_complete` URL. DingTalk: the
   `verification_uri_complete` URL (branded "OpenClaw" — that's DingTalk's
   onboarding bridge, safe). Give the user the URL, not ASCII. DingTalk QR
   expires in ~7200s; Feishu ~3600s; Weixin refreshes up to 3× on expiry.
5. **Confirm credentials landed in `.env`.** Use `read_file` on `~/.hermes/.env`
   and verify:
   - Weixin: `WEIXIN_ACCOUNT_ID`, `WEIXIN_TOKEN`, `WEIXIN_BASE_URL`, + DM/group policy.
   - Feishu: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_DOMAIN`,
     `FEISHU_CONNECTION_MODE=websocket`, + DM policy.
   - DingTalk: `DINGTALK_CLIENT_ID`, `DINGTALK_CLIENT_SECRET`, + DM policy.
     Stream Mode needs no public URL (unlike a webhook robot).
6. **Restart the gateway safely.** Never run `hermes gateway restart` from inside
   the gateway process tree. Restart via supervisor:
   - s6/Docker (Linux): `terminal(command="s6-svc -r /run/service/gateway-default")`
   - systemd (Linux): `terminal(command="systemctl restart hermes-gateway-default")`
   - macOS (launchd): `terminal(command="launchctl kickstart -k gui/$(id -u)/com.nousresearch.hermes.gateway")`
   - Windows: use `hermes gateway restart` from a separate shell (NOT the agent's
     own shell), or restart the Hermes service via `services.msc`.

   First check the agent's process tree — the agent is often in the dashboard
   tree, separate from the gateway, so restarting the gateway does NOT kill it.
7. **Verify** — see Verification.

## Pitfalls

1. **`hermes gateway setup` hangs the agent.** Interactive wizard; no per-platform
   non-interactive flag. Drive the QR flow via the bundled script.
2. **Import paths.** `save_env_value` is in `hermes_cli.config` (NOT `.gateway`).
   The feishu adapter is `plugins.platforms.feishu.adapter` (bundled plugin) — add
   both the install dir AND `<install_dir>/plugins` to `sys.path`.
3. **`hermes gateway restart` is blocked / self-killing.** The agent often runs
   inside the gateway process tree; restarting SIGTERMs it. Use the supervisor.
   The agent is frequently in the dashboard tree (separate from the gateway), so
   `s6-svc -r` is safe and does not kill the agent.
4. **Feishu: `lark_oapi` optional for registration, required to connect.** The QR
   flow is pure HTTP. On a sealed image (`HERMES_DISABLE_LAZY_INSTALLS=1` +
   `HERMES_LAZY_INSTALL_TARGET=<writable dir>`), install it with
   `from tools.lazy_deps import ensure; ensure("platform.feishu", prompt=False)`
   — this installs into the durable target, NOT the sealed venv. Do NOT `uv pip
   install` into the venv (Permission denied on sealed images). If PyPI is slow,
   set `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`.
5. **Credentials are dual-written (Weixin).** `qr_login()` writes
   `<home>/weixin/accounts/<id>.json`; the gateway reads `.env`
   (`WEIXIN_TOKEN`/`WEIXIN_ACCOUNT_ID`) at startup. Both must exist.
6. **Group chat won't work (Weixin).** iLink bot identities (`...@im.bot`) do not
   receive ordinary WeChat group events; `WEIXIN_GROUP_POLICY` has no effect.
   Feishu groups DO work (bot responds when @mentioned by default).
7. **"clawbot / openclaw" display name is Tencent's, not Hermes** (Weixin only).
   `ILINK_APP_ID="bot"` + `bot_type=3` select a pre-registered template; cosmetic.
8. **Feishu domain auto-switch.** If the scanned tenant is a Lark (international)
   org, `tenant_brand: "lark"` in the poll response switches the domain
   automatically; the saved `FEISHU_DOMAIN` reflects the final domain.
9. **Feishu QR expiry.** `expires_in` is ~3600s. If it lapses, re-run the driver
   for a fresh device-code flow.
10. **Transient `Disconnected (0.35s)` right after restart is normal** — the old
    gateway instance logs it as it exits; the new instance reconnects in seconds.
11. **To send a file/image to a channel from inside a session, do NOT use the
    `MEDIA:` tag** (that is the TTS voice marker). Call the adapter's one-shot
    sender directly, e.g. Weixin:
    `gateway.platforms.weixin.send_weixin_direct(extra={}, token=None,
    chat_id=<chat_id>, message="...", media_files=[(<abs_path>, False)])`.
    Get the chat_id from `<home>/channel_directory.json` (platform → entries →
    id). This is how to deliver a skill tarball or image to the user.
12. **DingTalk QR URL is branded "OpenClaw"** — that is DingTalk's onboarding
    bridge app name, not a different product. Safe to scan; it returns the
    `client_id`/`client_secret` for YOUR app. Don't confuse the user about it.
13. **DingTalk `ALLOWED_USERS=*` is the no-pairing wildcard** (source:
    `_is_user_allowed` returns True when `*` is present). Empty string means
    nobody allowed (pairing/deny), NOT "allow all". Don't set it empty unless
    the user asked for pairing.
14. **DingTalk uses Stream Mode** (long connection, no public URL needed) — the
    QR flow yields `client_id`/`client_secret` that the `dingtalk-stream` SDK
    uses directly. There is no `CONNECTION_MODE` env to set (unlike Feishu).
15. **The `process` tool session handle can go stale while the Python child is
    still running** (it reports `not_found` but `ps` shows the PID alive). If a
    QR poller seems to have vanished, check
    `terminal(command="pgrep -af connect_channel")` before re-launching;
    re-launching spawns a second QR and confuses which code the user should scan.

## Verification

```
search_files(pattern="weixin|feishu|dingtalk|platform\\(s\\)", path="<home>/logs/gateway.log")
```

The matches must show `✓ <channel> connected` and `Gateway running with N platform(s)`
(N+1). DingTalk connects in "Stream Mode" (`[Dingtalk] Connected via Stream Mode`),
Feishu in "websocket mode", Weixin shows `[Weixin] Connected account=...`. Send a
test DM from the phone; the log shows an inbound `platform=<channel>` message then
a `[<Channel>]` send. Confirm `.env` keys exist per Procedure step 5.
