#!/usr/bin/env python3
"""Отправляет Telegram-уведомление о публикации выпуска."""
import os
import sys
import urllib.request
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else ""
    token = os.environ.get("TG_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")

    if not token or not chat_id or not date:
        print("[WARN] TG_TOKEN / TG_CHAT_ID / date не заданы", file=sys.stderr)
        sys.exit(1)

    caption = f"📰 Нейрогазета — выпуск {date} опубликован\n\nЧитать: https://neurogazeta.ru/?date={date}"
    preview_img = os.path.join(REPO, "docs", "data", f"{date}_preview.png")

    api = f"https://api.telegram.org/bot{token}"

    if os.path.exists(preview_img):
        import http.client, mimetypes, uuid
        boundary = uuid.uuid4().hex
        with open(preview_img, "rb") as f:
            img_data = f.read()

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{chat_id}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="preview.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{api}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    else:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": caption}).encode()
        req = urllib.request.Request(f"{api}/sendMessage", data=data)

    with urllib.request.urlopen(req, timeout=30) as resp:
        print(resp.read().decode())


if __name__ == "__main__":
    main()
