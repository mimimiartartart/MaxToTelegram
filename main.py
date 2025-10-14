import asyncio
import json
import aiohttp
import os
import time
import base64
from urllib.parse import urlparse
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

load_dotenv()

# ========== Конфигурация ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MAX_TOKEN = os.getenv("MAX_TOKEN")

WS_URI = os.getenv("WS_URI", "wss://ws-api.oneme.ru/websocket")

USER_AGENT_PAYLOAD = {
    "deviceType": "WEB",
    "locale": os.getenv("LOCALE", "ru"),
    "deviceLocale": os.getenv("DEVICE_LOCALE", "ru"),
    "osVersion": os.getenv("OS_VERSION", "Windows"),
    "deviceName": os.getenv("DEVICE_NAME", "Edge"),
    "headerUserAgent": os.getenv("UA_HEADER", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
    "appVersion": os.getenv("APP_VERSION", "25.9.16"),
    "screen": os.getenv("SCREEN", "768x1024 1.0x"),
    "timezone": os.getenv("TIMEZONE", "Europe/Samara")
}
DEVICE_ID = os.getenv("DEVICE_ID", "b5c29d8e-6ef5-48fc-9618-1fe1eac4c1d3")

FRAMES_LOG = os.getenv("FRAMES_LOG", "frames.log")
SEEN_IDS_FILE = os.getenv("SEEN_IDS_FILE", "seen_ids.json")

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "20"))
RESUBSCRIBE_INTERVAL = int(os.getenv("RESUBSCRIBE_INTERVAL", "300"))
SUBSCRIBE_LIMIT = int(os.getenv("SUBSCRIBE_LIMIT", "500"))

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not MAX_TOKEN:
    raise SystemExit("ERROR: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and MAX_TOKEN must be set in .env")


bot = Bot(token=TELEGRAM_BOT_TOKEN)
seen_ids = set()
if os.path.exists(SEEN_IDS_FILE):
    try:
        with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
            seen_ids = set(json.load(f))
    except Exception:
        seen_ids = set()

pending_name_replies = {}
seq_counter = 0
seq_lock = asyncio.Lock()
chat_titles = {}
subscribed_chats = set()

async def next_seq():
    global seq_counter
    async with seq_lock:
        seq_counter += 1
        return seq_counter

async def save_seen_ids():
    try:
        with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_ids), f, ensure_ascii=False)
    except Exception as e:
        print("Ошибка сохранения seen_ids:", e, flush=True)

async def send_to_telegram(text: str):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        print("Ошибка отправки в Telegram:", e, flush=True)
        return False

async def request_user_name(ws, sender_id):
    seq = await next_seq()
    payload = {"contactIds": [int(sender_id)]}
    req = {"ver": 11, "cmd": 0, "seq": seq, "opcode": 32, "payload": payload}
    fut = asyncio.get_event_loop().create_future()
    pending_name_replies[seq] = fut
    try:
        await ws.send_str(json.dumps(req))
    except Exception:
        pending_name_replies.pop(seq, None)
        fut.cancel()
        return str(sender_id)
    try:
        name = await asyncio.wait_for(fut, timeout=5.0)
        return name or str(sender_id)
    except asyncio.TimeoutError:
        pending_name_replies.pop(seq, None)
        return str(sender_id)

# ========== Загрузка/сохранение вложений ==========
async def download_and_attach(session: aiohttp.ClientSession, attach_obj):
    """
    Поддерживает attach_obj как dict (previewData/baseUrl/photoToken/...) или как строку (URL).
    Возвращает локальный путь к сохранённому файлу или None.
    """
    os.makedirs("attachments", exist_ok=True)

    # normalize
    if isinstance(attach_obj, str):
        attach = {"baseUrl": attach_obj}
    elif isinstance(attach_obj, dict):
        attach = attach_obj
    else:
        return None

    # 1) сохранение previewData
    preview = attach.get("previewData")
    preview_path = None
    if isinstance(preview, str) and preview.startswith("data:"):
        try:
            header, b64 = preview.split(",", 1)
            mime = header.split(";")[0].split(":")[1] if ":" in header else "application/octet-stream"
            ext = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif"
            }.get(mime, ".bin")
            fn = f"preview_{int(time.time()*1000)}{ext}"
            preview_path = os.path.join("attachments", fn)
            with open(preview_path, "wb") as f:
                f.write(base64.b64decode(b64))
            print("[attach] saved previewData ->", preview_path, flush=True)
        except Exception as e:
            print("[attach] error saving previewData:", e, flush=True)
            preview_path = None

    # 2) попытка скачать baseUrl
    base_url = attach.get("baseUrl") or attach.get("url") or attach.get("file_url")
    if base_url:
        try:
            headers = {
                "User-Agent": USER_AGENT_PAYLOAD.get("headerUserAgent"),
                "Referer": "https://web.max.ru/"
            }
            # load cookies if exists (ws_auth.json)
            try:
                if os.path.exists("ws_auth.json"):
                    with open("ws_auth.json", "r", encoding="utf-8") as cf:
                        auth = json.load(cf)
                        cookies = auth.get("cookies") or []
                        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                        if cookie_header:
                            headers["Cookie"] = cookie_header
            except Exception:
                pass

            async with session.get(base_url, headers=headers, timeout=30) as r:
                ctype = r.headers.get("Content-Type", "")
                if r.status == 200 and ctype and ctype.startswith("image"):
                    if "webp" in ctype:
                        ext = ".webp"
                    elif "png" in ctype:
                        ext = ".png"
                    elif "jpeg" in ctype or "jpg" in ctype:
                        ext = ".jpg"
                    else:
                        p = urlparse(base_url).path
                        ext = os.path.splitext(p)[1] or ".bin"
                    fn = f"attach_{int(time.time()*1000)}{ext}"
                    out_path = os.path.join("attachments", fn)
                    with open(out_path, "wb") as of:
                        of.write(await r.read())
                    print("[attach] downloaded baseUrl ->", out_path, flush=True)
                    return out_path
                else:
                    print(f"[attach] baseUrl responded status={r.status}, content-type={ctype}", flush=True)
        except Exception as e:
            print("[attach] error downloading baseUrl:", e, flush=True)

    # 3) fallback -> preview if exists
    if preview_path:
        return preview_path

    # 4) photoToken: hint to use Playwright helper (not integrated here)
    photo_token = attach.get("photoToken") or attach.get("token")
    if photo_token:
        print("[attach] photoToken present — consider using external Playwright helper to fetch full image.", flush=True)

    return None

# подписка 
async def subscribe_to_chat(ws, chat_id):
    seq = await next_seq()
    msg = {"ver": 11, "cmd": 0, "seq": seq, "opcode": 75, "payload": {"chatId": chat_id, "subscribe": True}}
    try:
        await ws.send_str(json.dumps(msg))
        print(f"Отправлена подписка на чат {chat_id}", flush=True)
    except Exception as e:
        print("Ошибка отправки подписки:", e, flush=True)

# обработчик входящих фреймов
async def handle_incoming(data, ws, http_session):
    opcode = data.get("opcode")
    try:
        with open(FRAMES_LOG, "a", encoding="utf-8") as flog:
            flog.write(json.dumps(data, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if opcode == 75:
        payload = data.get("payload")
        print("Получен opcode 75 (subscribe ack). payload:", payload, flush=True)
        return

    if opcode == 32:
        payload = data.get("payload", {}) or {}
        contacts = payload.get("contacts", []) or []
        seq = data.get("seq")
        if seq and seq in pending_name_replies:
            name = None
            if contacts:
                c = contacts[0]
                name = (c.get("names") or [{}])[0].get("name") or c.get("id")
            fut = pending_name_replies.pop(seq, None)
            if fut and not fut.done():
                fut.set_result(name)
        return

    if opcode == 19:
        payload = data.get("payload", {}) or {}
        chats = payload.get("chats", []) or []
        print(f"Опкод 19: получены данные аккаунта/chats (найдено {len(chats)} чатов)", flush=True)
        count = 0
        for chat in chats:
            try:
                cid = chat.get("id")
                title = chat.get("title") or str(cid)
                chat_titles[str(cid)] = title
                if count < SUBSCRIBE_LIMIT:
                    await subscribe_to_chat(ws, cid)
                    subscribed_chats.add(str(cid))
                    count += 1
            except Exception:
                continue
        return

    if opcode == 64 or opcode == 128:
        payload = data.get("payload", {}) or {}
        if opcode == 64:
            msg = payload.get("message", {}) or {}
            chat_id = None
        else:
            chat_id = payload.get("chatId") or payload.get("chat_id")
            msg = payload.get("message", {}) or {}

        mid = msg.get("id") or f"{chat_id}_{int(time.time()*1000)}"
        if mid in seen_ids:
            return
        seen_ids.add(mid)
        if len(seen_ids) % 20 == 0:
            await save_seen_ids()

        sender = msg.get("sender")
        text = msg.get("text", "") or ""
        sender_name = str(sender)
        try:
            sender_name = await request_user_name(ws, sender)
        except Exception:
            pass

        attaches = msg.get("attaches") or msg.get("attachments") or []
        local_paths = []
        if attaches:
            dl_tasks = [download_and_attach(http_session, a) for a in attaches]
            results = await asyncio.gather(*dl_tasks)
            local_paths = [r for r in results if r]

        if opcode == 64:
            print("=== ЛС ===", flush=True)
            print(f"От: {sender_name} (id={sender})", flush=True)
            print("Текст:", text, flush=True)
            print("=========", flush=True)
            await send_to_telegram(f"ЛС от <b>{sender_name}</b>:\n\n<code>{text}</code>")
        else:
            title = chat_titles.get(str(chat_id), str(chat_id))
            print("=== ГРУППОВОЕ СООБЩЕНИЕ ===", flush=True)
            print(f"Чат: {title} ({chat_id})", flush=True)
            print(f"От: {sender_name} (id={sender})", flush=True)
            print(f"ID сообщения: {mid}", flush=True)
            print("Текст:", text, flush=True)
            if local_paths:
                print("Вложения сохранены:", flush=True)
                for p in local_paths:
                    print("  -", p, flush=True)
            print("===========================", flush=True)

            text_to_send = f"Новое сообщение в <b>{title}</b> от <b>{sender_name}</b>:\n\n{text}"
            await send_to_telegram(text_to_send)

            for p in local_paths:
                try:
                    low = p.lower()
                    file_obj = FSInputFile(p)
                    if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                        await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=file_obj)
                    else:
                        await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=file_obj)
                except Exception as e:
                    print("Ошибка отправки вложения в Telegram:", e, flush=True)

        return

    print("Прочий opcode:", opcode, "| payload snippet:", str(data.get("payload"))[:200], flush=True)

# периодическая переподписка
async def periodic_resubscribe(ws):
    try:
        while True:
            await asyncio.sleep(RESUBSCRIBE_INTERVAL)
            if not subscribed_chats:
                continue
            print("Периодическая повторная подписка на чаты...", flush=True)
            for cid in list(subscribed_chats):
                try:
                    await subscribe_to_chat(ws, int(cid))
                except Exception as e:
                    print("Ошибка при периодической подписке:", e, flush=True)
    except asyncio.CancelledError:
        return

async def ws_loop():
    backoff = 1
    max_backoff = 60
    headers = {
        "Origin": "https://web.max.ru",
        "Referer": "https://web.max.ru/",
        "User-Agent": USER_AGENT_PAYLOAD.get("headerUserAgent"),
    }

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                print("Попытка подключения к", WS_URI, flush=True)
                async with session.ws_connect(WS_URI, headers=headers, max_msg_size=0) as ws:
                    print("WS connected", flush=True)
                    backoff = 1

                    seq0 = await next_seq()
                    first_msg = {"ver": 11, "cmd": 0, "seq": seq0, "opcode": 6,
                                 "payload": {"userAgent": USER_AGENT_PAYLOAD, "deviceId": DEVICE_ID}}
                    await ws.send_str(json.dumps(first_msg))
                    await asyncio.sleep(0.08)

                    seq1 = await next_seq()
                    second_msg = {"ver": 11, "cmd": 0, "seq": seq1, "opcode": 19,
                                  "payload": {"interactive": True, "token": MAX_TOKEN,
                                              "chatsCount": 40, "chatsSync": 0, "contactsSync": 0,
                                              "presenceSync": 0, "draftsSync": 0}}
                    await ws.send_str(json.dumps(second_msg))

                    # JSON-heartbeat
                    async def json_heartbeat():
                        try:
                            while True:
                                seqh = await next_seq()
                                hb = {"ver": 11, "cmd": 0, "seq": seqh, "opcode": 1, "payload": {"interactive": False}}
                                try:
                                    await ws.send_str(json.dumps(hb))
                                except Exception:
                                    return
                                await asyncio.sleep(HEARTBEAT_INTERVAL)
                        except asyncio.CancelledError:
                            return

                    hb_task = asyncio.create_task(json_heartbeat())
                    resub_task = asyncio.create_task(periodic_resubscribe(ws))

                    print("Запущены heartbeat и periodic resubscribe", flush=True)

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except Exception:
                                continue
                            try:
                                await handle_incoming(data, ws, session)
                            except Exception as e:
                                print("Ошибка в обработке incoming:", e, flush=True)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print("WS ERROR:", msg, flush=True)
                            break

                    hb_task.cancel()
                    resub_task.cancel()
                    print("WS connection closed, будем переподключаться...", flush=True)

            except Exception as e:
                print("WS connection failed / crashed:", e, flush=True)
                print(f"Reconnect in {backoff} seconds...", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

async def main():
    try:
        await ws_loop()
    finally:
        await bot.session.close()
        await save_seen_ids()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Выход по Ctrl+C", flush=True)
