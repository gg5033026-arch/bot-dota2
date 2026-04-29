from __future__ import annotations
from typing import TYPE_CHECKING
import os
import re
import json
import time
import logging
import html
from telebot.types import Message
from FunPayAPI.updater.events import NewMessageEvent

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("AutoData")

NAME        = "AutoData"
VERSION     = "1.1"
DESCRIPTION = "Автовыдача данных по команде с проверкой аренды и чёрного списка."
CREDITS     = "@snoopyseller"
UUID        = "c425b88f-c4df-4323-9ce6-eae5783ecbec"
SETTINGS_PAGE = False

# ===================== ХРАНИЛИЩЕ =====================

PLUGIN_FOLDER = "storage/plugins/autodata"
DATA_FILE     = os.path.join(PLUGIN_FOLDER, "data.json")
NOTIFY_FILE   = os.path.join(PLUGIN_FOLDER, "notify_settings.json")

os.makedirs(PLUGIN_FOLDER, exist_ok=True)
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)
if not os.path.exists(NOTIFY_FILE):
    with open(NOTIFY_FILE, "w", encoding="utf-8") as f:
        # По умолчанию уведомления включены для всех команд
        json.dump({"enabled": True, "commands": {}}, f, indent=4, ensure_ascii=False)


def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_notify() -> dict:
    try:
        with open(NOTIFY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": True, "commands": {}}


def save_notify(data: dict):
    with open(NOTIFY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def should_notify(command: str) -> bool:
    """Проверяет, нужно ли слать уведомление для данной команды."""
    cfg = load_notify()
    # Сначала проверяем настройку для конкретной команды
    cmd_settings = cfg.get("commands", {})
    if command.lower() in cmd_settings:
        return bool(cmd_settings[command.lower()])
    # Иначе используем глобальную настройку
    return bool(cfg.get("enabled", True))


# ===================== ЧЁРНЫЙ СПИСОК =====================

def is_blacklisted(username: str, cardinal: "Cardinal") -> bool:
    """
    Проверяет, находится ли пользователь в чёрном списке Cardinal.
    cardinal.blacklist — set/list юзернеймов (sidor0912/FunPayCardinal).
    """
    if not username:
        return False
    bl = getattr(cardinal, "blacklist", None)
    if not bl:
        return False
    return username.strip().lower() in {u.strip().lower() for u in bl}


# ===================== ПРОВЕРКА АРЕНДЫ =====================

RENTAL_ACTIVE_FILE = "storage/plugins/rental_accounts/active.json"


def check_active_rental(chat_id, lot_keyword: str) -> bool:
    """
    Возвращает True, если у покупателя есть активная аренда с нужным ключевым словом.
    Если lot_keyword пустой — проверка не нужна.
    """
    if not lot_keyword:
        return True
    try:
        with open(RENTAL_ACTIVE_FILE, "r", encoding="utf-8") as f:
            active = json.load(f)
    except Exception:
        return True
    now = time.time()
    kw = lot_keyword.strip().lower()
    for rental in active.values():
        if str(rental.get("chat_id", "")) == str(chat_id):
            if rental.get("end_ts", 0) > now:
                if kw in rental.get("lot_name", "").lower():
                    return True
    return False


# ===================== FSM =====================

user_states: dict[int, dict] = {}


def adddata_start(message: Message, cardinal: "Cardinal"):
    user_states[message.chat.id] = {"step": "command"}
    cardinal.telegram.bot.send_message(
        message.chat.id,
        "⌨️ Введите команду (фразу), по которой покупатель получит данные.\nМожно использовать /команды.\nДля отмены: /cancel"
    )


def deldata_start(message: Message, cardinal: "Cardinal"):
    uid = str(message.chat.id)
    data = load_data()
    if uid not in data or not data[uid]:
        cardinal.telegram.bot.send_message(message.chat.id, "❌ У вас нет записей.")
        return
    user_states[message.chat.id] = {"step": "del_target"}
    cardinal.telegram.bot.send_message(
        message.chat.id,
        "🗑 Введите команду для удаления.\nДля отмены: /cancel"
    )


def fsm_handler(message: Message, cardinal: "Cardinal"):
    chat_id = message.chat.id
    if chat_id not in user_states:
        return
    # Отмена FSM только по /cancel, чтобы можно было сохранять команды вида /start
    if (message.text or "").strip().lower() == "/cancel":
        user_states.pop(chat_id, None)
        cardinal.telegram.bot.send_message(chat_id, "❎ Действие отменено.")
        return

    state = user_states[chat_id]
    data  = load_data()
    uid   = str(chat_id)

    # ── Удаление ──────────────────────────────────────────────────────────────
    if state["step"] == "del_target":
        target = message.text.strip().lower()
        before = len(data.get(uid, []))
        data[uid] = [e for e in data.get(uid, []) if e["command"].lower() != target]
        if len(data.get(uid, [])) < before:
            save_data(data)
            cardinal.telegram.bot.send_message(chat_id, "✅ Запись удалена.")
        else:
            cardinal.telegram.bot.send_message(chat_id, "❌ Команда не найдена.")
        user_states.pop(chat_id, None)
        return

    # ── Добавление ────────────────────────────────────────────────────────────
    if state["step"] == "command":
        cmd = message.text.strip()
        if any(e["command"].lower() == cmd.lower() for e in data.get(uid, [])):
            cardinal.telegram.bot.send_message(chat_id, "❌ Такая команда уже есть.")
            user_states.pop(chat_id, None)
            return
        state["command"] = cmd
        state["step"]    = "response"
        cardinal.telegram.bot.send_message(
            chat_id,
            (
                "📝 Введите текст, который получит покупатель.\n\n"
                "Можно использовать несколько строк — вся пересланная пользователем строка\n"
                "сохранится как есть (логин, пароль, ссылки и т.д.)."
            )
        )

    elif state["step"] == "response":
        state["response"] = message.text  # сохраняем как есть, с переносами
        state["step"]     = "lot_keyword"
        cardinal.telegram.bot.send_message(
            chat_id,
            (
                "🔑 Введите ключевое слово лота аренды для проверки доступа\n"
                "(например: <code>chatgpt</code>, <code>netflix</code>).\n\n"
                "Покупатель получит данные только при активной аренде лота "
                "с этим словом в названии.\n\n"
                "Введите <code>-</code>, если проверка не нужна."
            ),
            parse_mode="HTML"
        )

    elif state["step"] == "lot_keyword":
        raw         = message.text.strip()
        lot_keyword = "" if raw == "-" else raw

        entry = {
            "command":     state["command"],
            "response":    state["response"],
            "lot_keyword": lot_keyword,
        }
        data.setdefault(uid, []).append(entry)
        save_data(data)

        kw_txt = f"<code>{html.escape(lot_keyword)}</code>" if lot_keyword else "не задано (без проверки)"
        cardinal.telegram.bot.send_message(
            chat_id,
            (
                "✅ Запись добавлена.\n"
                f"💬 Команда: <code>{html.escape(entry['command'])}</code>\n"
                f"🔑 Ключевое слово лота: {kw_txt}\n"
                f"📝 Ответ:\n<pre>{html.escape(entry['response'][:300])}</pre>"
            ),
            parse_mode="HTML"
        )
        user_states.pop(chat_id, None)


def listdata_handler(message: Message, cardinal: "Cardinal"):
    uid  = str(message.chat.id)
    data = load_data()
    if uid not in data or not data[uid]:
        cardinal.telegram.bot.send_message(message.chat.id, "❌ У вас нет записей.")
        return
    lines = []
    for i, entry in enumerate(data[uid], 1):
        kw  = entry.get("lot_keyword") or "—"
        preview = entry["response"].replace("\n", " ")[:60]
        lines.append(
            f"{i}. 💬 <code>{html.escape(str(entry['command']))}</code> | 🔑 {html.escape(str(kw))}\n"
            f"   📝 {preview}…"
        )
    cardinal.telegram.bot.send_message(
        message.chat.id,
        "📜 Записи автовыдачи:\n\n" + "\n\n".join(lines),
        parse_mode="HTML"
    )


# ===================== ОБРАБОТКА СООБЩЕНИЙ =====================

def new_message_handler(cardinal: "Cardinal", event: NewMessageEvent):
    try:
        text = (getattr(event.message, "text", "") or "").strip()
        if not text:
            return

        text_l   = text.lower()
        buyer_id = str(getattr(event.message, "chat_id", ""))
        username = str(getattr(event.message, "author", "") or "")

        data = load_data()

        for uid, entries in data.items():
            for entry in entries:
                if text_l != entry["command"].lower():
                    continue

                # ── Чёрный список ────────────────────────────────────────────
                if is_blacklisted(username, cardinal):
                    logger.info(f"[AutoData] {username} в ЧС — команда '{entry['command']}' проигнорирована.")
                    return  # молча игнорируем

                # ── Проверка аренды ──────────────────────────────────────────
                lot_keyword = entry.get("lot_keyword", "")
                if not check_active_rental(buyer_id, lot_keyword):
                    cardinal.account.send_message(
                        event.message.chat_id,
                        "❌ У вас нет активной аренды для получения этих данных.\n"
                        "Приобретите аренду на FunPay."
                    )
                    return

                # ── Выдача данных ────────────────────────────────────────────
                cardinal.account.send_message(
                    event.message.chat_id,
                    entry["response"]
                )
                logger.info(f"[AutoData] Выдано '{entry['command']}' → chat_id={buyer_id} ({username})")

                # ── Управление уведомлением Cardinal ─────────────────────────
                if not should_notify(entry["command"]):
                    try:
                        if hasattr(cardinal.account, "mark_as_read"):
                            cardinal.account.mark_as_read(event.message.chat_id)
                    except Exception:
                        pass
                    try:
                        event.message.is_new = False
                    except Exception:
                        pass
                else:
                    # Отправляем собственное уведомление владельцу
                    try:
                        owner_tg_id = int(uid)
                        notify_text = (
                            f"📤 Выдача данных:\n"
                            f"💬 Команда: <code>{html.escape(entry['command'])}</code>\n"
                            f"👤 Покупатель: {username or buyer_id}"
                        )
                        cardinal.telegram.bot.send_message(
                            owner_tg_id, notify_text, parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.debug(f"[AutoData] Уведомление не отправлено: {e}")
                # ─────────────────────────────────────────────────────────────
                return

    except Exception as e:
        logger.exception(f"[AutoData] new_message_handler error: {e}")


# ===================== УПРАВЛЕНИЕ УВЕДОМЛЕНИЯМИ =====================

_notify_states: dict[int, dict] = {}


def datanotify_handler(message: Message, cardinal: "Cardinal"):
    """
    /datanotify — управление уведомлениями автовыдачи.
    Показывает текущие настройки и предлагает варианты.
    """
    cfg  = load_notify()
    data = load_data()
    uid  = str(message.chat.id)

    global_on = cfg.get("enabled", True)
    cmd_cfg   = cfg.get("commands", {})

    # Собираем все команды
    all_cmds = [e["command"] for e in data.get(uid, [])]

    lines = [f"🔔 Глобальные уведомления: {'✅ ВКЛ' if global_on else '❌ ВЫКЛ'}"]
    if all_cmds:
        lines.append("\nНастройки по командам:")
        for cmd in all_cmds:
            state = cmd_cfg.get(cmd.lower())
            if state is None:
                status = "по умолчанию"
            elif state:
                status = "✅ ВКЛ"
            else:
                status = "❌ ВЫКЛ"
            lines.append(f"  • <code>{cmd}</code> — {status}")

    lines.append(
        "\n<b>Команды:</b>\n"
        "/datanotify on — включить все\n"
        "/datanotify off — выключить все\n"
        "/datanotify on !команда — включить для конкретной\n"
        "/datanotify off !команда — выключить для конкретной\n"
        "/datanotify reset !команда — сбросить к глобальному"
    )

    cardinal.telegram.bot.send_message(
        message.chat.id,
        "\n".join(lines),
        parse_mode="HTML"
    )

    # Обработка аргументов прямо в команде
    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) < 2:
        return

    action = parts[1].lower()
    cfg    = load_notify()

    if len(parts) == 2:
        # /datanotify on|off — глобальная настройка
        if action == "on":
            cfg["enabled"] = True
            save_notify(cfg)
            cardinal.telegram.bot.send_message(
                message.chat.id, "✅ Уведомления включены для всех команд."
            )
        elif action == "off":
            cfg["enabled"] = False
            save_notify(cfg)
            cardinal.telegram.bot.send_message(
                message.chat.id, "❌ Уведомления выключены для всех команд."
            )

    elif len(parts) == 3:
        # /datanotify on|off|reset !команда
        cmd = parts[2].strip().lower()
        if action == "on":
            cfg.setdefault("commands", {})[cmd] = True
            save_notify(cfg)
            cardinal.telegram.bot.send_message(
                message.chat.id,
                f"✅ Уведомления включены для <code>{cmd}</code>.",
                parse_mode="HTML"
            )
        elif action == "off":
            cfg.setdefault("commands", {})[cmd] = False
            save_notify(cfg)
            cardinal.telegram.bot.send_message(
                message.chat.id,
                f"❌ Уведомления выключены для <code>{cmd}</code>.",
                parse_mode="HTML"
            )
        elif action == "reset":
            cfg.get("commands", {}).pop(cmd, None)
            save_notify(cfg)
            cardinal.telegram.bot.send_message(
                message.chat.id,
                f"🔄 Настройка для <code>{cmd}</code> сброшена к глобальной.",
                parse_mode="HTML"
            )


# ===================== ИНИЦИАЛИЗАЦИЯ =====================


# ===================== РЕДАКТИРОВАНИЕ ЗАПИСИ =====================

_edit_states: dict[int, dict] = {}


def editdata_start(message: Message, cardinal: "Cardinal"):
    uid  = str(message.chat.id)
    data = load_data()

    if uid not in data or not data[uid]:
        cardinal.telegram.bot.send_message(message.chat.id, "❌ Нет записей для редактирования.")
        return

    lines = []
    for i, entry in enumerate(data[uid], 1):
        kw      = entry.get("lot_keyword") or "—"
        preview = entry["response"].replace("\n", " ")[:60]
        lines.append(f"{i}. 💬 <code>{html.escape(str(entry['command']))}</code> | 🔑 {html.escape(str(kw))}\n   📝 {html.escape(preview)}…")

    _edit_states[message.chat.id] = {"step": "pick"}
    cardinal.telegram.bot.send_message(
        message.chat.id,
        "✏️ Выберите запись для редактирования (введите номер).\nДля отмены: /cancel\n\n" + "\n\n".join(lines),
        parse_mode="HTML"
    )


def editdata_fsm(message: Message, cardinal: "Cardinal"):
    chat_id = message.chat.id
    if chat_id not in _edit_states:
        return
    if (message.text or "").strip().lower() == "/cancel":
        _edit_states.pop(chat_id, None)
        cardinal.telegram.bot.send_message(chat_id, "❎ Редактирование отменено.")
        return

    st   = _edit_states[chat_id]
    uid  = str(chat_id)
    data = load_data()
    txt  = (message.text or "").strip()

    if st["step"] == "pick":
        try:
            idx = int(txt) - 1
            entries = data.get(uid, [])
            if idx < 0 or idx >= len(entries):
                raise ValueError
        except (ValueError, IndexError):
            cardinal.telegram.bot.send_message(
                chat_id, f"❌ Введите число от 1 до {len(data.get(uid, []))}."
            )
            return

        entry = data[uid][idx]
        st["idx"] = idx
        st["step"] = "choose_field"

        cardinal.telegram.bot.send_message(
            chat_id,
            f"✏️ Запись: <code>{html.escape(entry['command'])}</code>\n\n"
            "Что хотите изменить?\n"
            "1. Текст выдачи\n"
            "2. Команду\n"
            "3. Ключевое слово лота\n\n"
            "Введите номер:",
            parse_mode="HTML"
        )

    elif st["step"] == "choose_field":
        if txt == "1":
            st["field"] = "response"
            st["step"]  = "new_value"
            entry = data[uid][st["idx"]]
            cardinal.telegram.bot.send_message(
                chat_id,
                f"📝 Текущий текст выдачи:\n\n<pre>{html.escape(entry['response'][:500])}</pre>\n\n"
                "Введите новый текст:",
                parse_mode="HTML"
            )
        elif txt == "2":
            st["field"] = "command"
            st["step"]  = "new_value"
            entry = data[uid][st["idx"]]
            cardinal.telegram.bot.send_message(
                chat_id,
                f"💬 Текущая команда: <code>{html.escape(entry['command'])}</code>\n\n"
                "Введите новую команду:",
                parse_mode="HTML"
            )
        elif txt == "3":
            st["field"] = "lot_keyword"
            st["step"]  = "new_value"
            entry = data[uid][st["idx"]]
            cur   = entry.get("lot_keyword") or "не задано"
            cardinal.telegram.bot.send_message(
                chat_id,
                f"🔑 Текущее ключевое слово: <code>{html.escape(cur)}</code>\n\n"
                "Введите новое ключевое слово (или <code>-</code> чтобы убрать):",
                parse_mode="HTML"
            )
        else:
            cardinal.telegram.bot.send_message(chat_id, "❌ Введите 1, 2 или 3.")

    elif st["step"] == "new_value":
        field = st["field"]
        idx   = st["idx"]

        if field == "lot_keyword":
            new_val = "" if txt == "-" else txt
        else:
            new_val = message.text  # сохраняем как есть (с переносами)

        data[uid][idx][field] = new_val
        save_data(data)
        _edit_states.pop(chat_id, None)

        field_names = {"response": "текст выдачи", "command": "команда", "lot_keyword": "ключевое слово"}
        cardinal.telegram.bot.send_message(
            chat_id,
            f"✅ <b>{field_names.get(field, field)}</b> обновлён!\n"
            f"💬 Команда: <code>{html.escape(data[uid][idx]['command'])}</code>",
            parse_mode="HTML"
        )


def init_cardinal(cardinal: "Cardinal"):
    tg = cardinal.telegram
    tg.msg_handler(lambda m: adddata_start(m, cardinal),    commands=["adddata"])
    tg.msg_handler(lambda m: editdata_start(m, cardinal),   commands=["editdata"])
    tg.msg_handler(lambda m: deldata_start(m, cardinal),    commands=["deldata"])
    tg.msg_handler(lambda m: listdata_handler(m, cardinal), commands=["listdata"])
    tg.msg_handler(lambda m: datanotify_handler(m, cardinal), commands=["datanotify"])
    tg.msg_handler(
        lambda m: fsm_handler(m, cardinal),
        func=lambda m: m.chat.id in user_states
    )
    tg.msg_handler(
        lambda m: editdata_fsm(m, cardinal),
        func=lambda m: m.chat.id in _edit_states
    )

    cardinal.add_telegram_commands(UUID, [
        ("adddata",    "Добавить запись автовыдачи",    True),
        ("editdata",   "Редактировать запись",          True),
        ("deldata",    "Удалить запись",                True),
        ("listdata",   "Список записей",                True),
        ("datanotify", "Настройка уведомлений",         True),
    ])


BIND_TO_PRE_INIT   = [init_cardinal]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_DELETE     = None
