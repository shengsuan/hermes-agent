#!/usr/bin/env python3
"""Connect Hermes to a messaging channel via QR onboarding — no pairing by default.

Channels:
  weixin   — personal WeChat via iLink Bot API (gateway.platforms.weixin.qr_login)
  feishu   — Feishu/Lark via scan-to-create (plugins.platforms.feishu.adapter.qr_register)
  dingtalk — DingTalk via device-flow QR (hermes_cli.dingtalk_auth)

Access policy (who can DM the bot after scan):
  (default)  no-pairing   — open DM for anyone
  --pairing               — pairing/approval handshake
  --allow id1,id2         — explicit allowlist of user IDs

Usage (run BACKGROUND so it keeps polling while you relay the scan URL):

    HERMES_INSTALL=<install_dir> <hermes_venv_python> connect_channel.py <channel> [--pairing|--allow ids]

Env:
  HERMES_INSTALL  Hermes install dir (default /opt/hermes) — added to sys.path.
  FEISHU_DOMAIN   initial domain for feishu: 'feishu' (China) or 'lark'.
"""

import argparse
import asyncio
import json
import os
import sys
import time

INSTALL_DIR = os.environ.get("HERMES_INSTALL", "/opt/hermes")
for p in (INSTALL_DIR, os.path.join(INSTALL_DIR, "plugins")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _home() -> str:
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    try:
        from hermes_cli.sessions_cmd import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return os.path.expanduser("~/.hermes")


def _save_env(key: str, value: str) -> None:
    from hermes_cli.config import save_env_value  # NOTE: hermes_cli.config, not .gateway
    save_env_value(key, value)


# ---------------------------------------------------------------------------
# Weixin
# ---------------------------------------------------------------------------
async def connect_weixin(policy: str, allow: str) -> None:
    try:
        from gateway.platforms.weixin import check_weixin_requirements
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"import gateway failed: {exc}"}))
        return
    if not check_weixin_requirements():
        print(json.dumps({"ok": False, "error": "missing deps: aiohttp+cryptography required"}))
        return

    from gateway.platforms.weixin import qr_login
    try:
        creds = await qr_login(_home())
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "cancelled"}))
        return
    if not creds:
        print(json.dumps({"ok": False, "error": "no_credentials_or_timeout"}))
        return

    account_id = creds.get("account_id", "")
    token = creds.get("token", "")
    base_url = creds.get("base_url", "")
    if not account_id or not token:
        print(json.dumps({"ok": False, "error": "incomplete_credentials"}))
        return

    _save_env("WEIXIN_ACCOUNT_ID", account_id)
    _save_env("WEIXIN_TOKEN", token)
    if base_url:
        _save_env("WEIXIN_BASE_URL", base_url)
    _save_env("WEIXIN_GROUP_POLICY", "disabled")  # iLink bots can't do groups anyway

    if policy == "pairing":
        _save_env("WEIXIN_DM_POLICY", "pairing")
        _save_env("WEIXIN_ALLOW_ALL_USERS", "false")
    elif policy == "allowlist":
        _save_env("WEIXIN_DM_POLICY", "allowlist")
        _save_env("WEIXIN_ALLOW_ALL_USERS", "false")
        _save_env("WEIXIN_ALLOWED_USERS", allow)
    else:  # no-pairing (default)
        _save_env("WEIXIN_DM_POLICY", "open")
        _save_env("WEIXIN_ALLOW_ALL_USERS", "true")

    print(json.dumps({"ok": True, "account_id": account_id, "policy": policy}), flush=True)


# ---------------------------------------------------------------------------
# Feishu
# ---------------------------------------------------------------------------
def connect_feishu(policy: str, allow: str) -> None:
    try:
        from plugins.platforms.feishu.adapter import qr_register
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"import feishu adapter failed: {exc}"}))
        return

    initial_domain = os.environ.get("FEISHU_DOMAIN", "feishu")
    try:
        creds = qr_register(initial_domain=initial_domain, timeout_seconds=600)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "cancelled"}))
        return
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"qr_register failed: {exc}"}))
        return

    if not creds or not creds.get("app_id") or not creds.get("app_secret"):
        print(json.dumps({"ok": False, "error": "no_credentials_or_timeout"}))
        return

    app_id = creds["app_id"]
    app_secret = creds["app_secret"]
    domain = creds.get("domain") or initial_domain

    _save_env("FEISHU_APP_ID", app_id)
    _save_env("FEISHU_APP_SECRET", app_secret)
    _save_env("FEISHU_DOMAIN", domain)
    _save_env("FEISHU_CONNECTION_MODE", "websocket")  # QR path always websocket

    if policy == "pairing":
        _save_env("FEISHU_ALLOW_ALL_USERS", "false")
        _save_env("FEISHU_ALLOWED_USERS", "")
    elif policy == "allowlist":
        _save_env("FEISHU_ALLOW_ALL_USERS", "false")
        _save_env("FEISHU_ALLOWED_USERS", allow)
    else:  # no-pairing (default)
        _save_env("FEISHU_ALLOW_ALL_USERS", "true")
        _save_env("FEISHU_ALLOWED_USERS", "")

    print(json.dumps({
        "ok": True,
        "app_id": app_id,
        "domain": domain,
        "policy": policy,
        "bot_name": creds.get("bot_name"),
        "open_id": creds.get("open_id"),
    }), flush=True)


# ---------------------------------------------------------------------------
# DingTalk
# ---------------------------------------------------------------------------
def connect_dingtalk(policy: str, allow: str) -> None:
    try:
        from hermes_cli.dingtalk_auth import begin_registration, poll_registration, RegistrationError
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"import dingtalk_auth failed: {exc}"}))
        return

    try:
        reg = begin_registration()
    except RegistrationError as exc:
        print(json.dumps({"ok": False, "error": f"init failed: {exc}"}))
        return

    device_code = reg["device_code"]
    url = reg["verification_uri_complete"]
    interval = reg["interval"]
    expires_in = reg["expires_in"]

    print(json.dumps({
        "ok": True, "phase": "awaiting_scan",
        "qr_url": url, "expires_in": expires_in, "interval": interval,
    }), flush=True)

    deadline = time.monotonic() + min(expires_in, 600)
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            r = poll_registration(device_code)
        except RegistrationError:
            continue
        status = r["status"]
        if status == "SUCCESS":
            cid, csec = r["client_id"], r["client_secret"]
            if not cid or not csec:
                print(json.dumps({"ok": False, "error": "authorized but creds missing"}))
                return
            _save_env("DINGTALK_CLIENT_ID", cid)
            _save_env("DINGTALK_CLIENT_SECRET", csec)
            if policy == "pairing":
                _save_env("DINGTALK_ALLOWED_USERS", "")
            elif policy == "allowlist":
                _save_env("DINGTALK_ALLOWED_USERS", allow)
            else:  # no-pairing (default): "*" = allow anyone
                _save_env("DINGTALK_ALLOWED_USERS", "*")
            print(json.dumps({"ok": True, "phase": "success", "client_id": cid}), flush=True)
            return
        if status in ("FAIL", "EXPIRED"):
            print(json.dumps({"ok": False, "error": r.get("fail_reason") or status}))
            return

    print(json.dumps({"ok": False, "error": "poll_timeout"}))
    return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("channel", choices=["weixin", "feishu", "dingtalk"])
    ap.add_argument("--pairing", action="store_true", help="use pairing/approval mode")
    ap.add_argument("--allow", default="", help="comma-separated user IDs for allowlist mode")
    args = ap.parse_args()

    policy = "pairing" if args.pairing else ("allowlist" if args.allow else "open")

    if args.channel == "weixin":
        asyncio.run(connect_weixin(policy, args.allow))
    elif args.channel == "feishu":
        connect_feishu(policy, args.allow)
    else:
        connect_dingtalk(policy, args.allow)


if __name__ == "__main__":
    main()
