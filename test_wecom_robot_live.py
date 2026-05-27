"""Send a one-off live message to a WeCom group robot webhook."""

from __future__ import annotations

import argparse
import os
import sys

import requests


DEFAULT_WEBHOOK_URL = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    "?key=9d4e523e-5ed1-4825-a66e-a1986711d350"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a test message to a WeCom group robot.")
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("WECOM_WEBHOOK_URL", DEFAULT_WEBHOOK_URL),
        help="WeCom robot webhook URL; defaults to WECOM_WEBHOOK_URL or the configured test robot.",
    )
    parser.add_argument("--message", default="testing", help="Text message to send.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    webhook_url = args.webhook_url
    if not webhook_url.startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send"):
        print("Set WECOM_WEBHOOK_URL to a valid WeCom robot webhook URL.", file=sys.stderr)
        return 1

    response = requests.post(
        webhook_url,
        json={"msgtype": "text", "text": {"content": args.message}},
        timeout=10,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("errcode") != 0:
        print(f"WeCom API rejected the message: {result}", file=sys.stderr)
        if result.get("errcode") == 93000:
            print(
                "The webhook key is invalid. Recreate or re-copy the group robot webhook, "
                "then run this script with the new URL.",
                file=sys.stderr,
            )
        return 1

    print(f"WeCom robot message sent: {args.message}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as error:
        print(f"Request failed: {error}", file=sys.stderr)
        raise SystemExit(1)
