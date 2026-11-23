"""LLM: клиенты, ответы, память между запусками, генерация кода и tool-calling роутер."""
import os
import re
import json
import threading

import openai

from settings import (OPENROUTER_API_KEY, GROQ_API_KEY, HTTP_TIMEOUT, CONNECT_TIMEOUT, HISTORY_FILE,
                      MAX_CONTEXT_MESSAGES, MAX_STORED_MESSAGES, ROUTER_MODEL, FAST_MODEL, CODE_MODEL)
from text_tts import extract_emotion, EMOTIONS
from clock import now_for_prompt

openrouter_client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY,
    timeout=openai.Timeout(HTTP_TIMEOUT, connect=CONNECT_TIMEOUT), max_retries=0,
) if OPENROUTER_API_KEY else None

groq_client = openai.OpenAI(
    base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY,
    timeout=openai.Timeout(HTTP_TIMEOUT, connect=CONNECT_TIMEOUT), max_retries=0,
) if GROQ_API_KEY else None

ollama_client = openai.OpenAI(
    base_url="http://localhost:11434/v1", api_key="ollama",
    timeout=openai.Timeout(60.0, connect=3.0), max_retries=0,
)

ONLINE_MODELS = {
    "[CHAT]": {"client": groq_client, "model": FAST_MODEL},
    "[CODE]": {"client": groq_client, "model": CODE_MODEL},
    "[TEXT]": {"client": groq_client, "model": FAST_MODEL},
}
GROQ_FALLBACK_MODELS = {
    "[CHAT]": {"client": openrouter_client, "model": "google/gemma-4-26b-a4b-it:free"},
    "[CODE]": {"client": openrouter_client, "model": "deepseek/deepseek-r1:free"},
    "[TEXT]": {"client": openrouter_client, "model": "google/gemma-4-26b-a4b-it:free"},
}
LOCAL_MODELS = {
    "[CHAT]": {"client": ollama_client, "model": "qwen2.5:7b"},
    "[CODE]": {"client": ollama_client, "model": "qwen2.5-coder:7b"},
    "[TEXT]": {"client": ollama_client, "model": "qwen2.5:7b"},
}

# gpt-oss — рассуждающие модели: при маленьком max_tokens рассуждение съедает лимит и ответ пустой.
# Просим reasoning_effort=low; если сервер не принимает параметр — запоминаем и больше не шлём.
_no_reasoning_param = set()


def _chat(client, model, messages, max_tokens=800, temperature=0.4, **extra):
    if client is None:
        return None
    kwargs = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, **extra)
    use_effort = client is groq_client and model.startswith("openai/gpt-oss") and model not in _no_reasoning_param
    if use_effort:
        kwargs["extra_body"] = {"reasoning_effort": "low"}
    try:
        return client.chat.completions.create(**kwargs)
    except openai.APIStatusError as e:
        if use_effort and e.status_code == 400:
            _no_reasoning_param.add(model)
            kwargs.pop("extra_body", None)
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as e2:
                print(f"   ⚠ {model}: {type(e2).__name__}: {e2}")
                return None
        print(f"   ⚠ {model}: {e.status_code}")
    except openai.APITimeoutError:
        print(f"   ⏱ {model}")
    except openai.APIConnectionError:
        print(f"   🔌 {model}")
    except Exception as e:
        print(f"   ❌ {model}: {type(e).__name__}: {e}")
    return None


def _try_llm(client, model, messages, max_tokens=800, temperature=0.4):
    r = _chat(client, model, messages, max_tokens=max_tokens, temperature=temperature)
    if r is None or not r.choices:
        return None
    return r.choices[0].message.content


# ==================== ПАМЯТЬ ====================
class Memory:
    """История сохраняется в memory.json и переживает перезапуск.
    Когда сообщений больше MAX_STORED_MESSAGES, старые сжимаются в короткую сводку."""

    def __init__(self, path=HISTORY_FILE):
        self.path = path
        self.summary = ""
        self.messages = []
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()     # запись файла: два потока не должны одновременно писать один .tmp
        self._summarizing = False
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.summary = data.get("summary", "")
                self.messages = data.get("messages", [])[-MAX_STORED_MESSAGES:]
        except Exception as e:
            print(f"⚠️ memory load: {e}")

    def save(self):
        try:
            with self._lock:
                data = {"summary": self.summary, "messages": list(self.messages)}
            with self._io_lock:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
        except Exception as e:
            print(f"⚠️ memory save: {e}")

    def push(self, role, content):
        if not content:
            return
        with self._lock:
            self.messages.append({"role": role, "content": content})
            need = len(self.messages) > MAX_STORED_MESSAGES and not self._summarizing
            if need:
                self._summarizing = True
        self.save()
        if need:
            threading.Thread(target=self._summarize, daemon=True).start()

    def context(self, n=MAX_CONTEXT_MESSAGES):
        with self._lock:
            return list(self.messages[-n:])

    def clear(self):
        with self._lock:
            self.messages.clear()
            self.summary = ""
        self.save()

    def _summarize(self):
        try:
            with self._lock:
                old = self.messages[:-MAX_CONTEXT_MESSAGES]
            if not old:
                return
            dialog = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in old)
            prompt = (f"Старая сводка:\n{self.summary or '(пусто)'}\n\nНовые реплики:\n{dialog}\n\n"
                      "Обнови сводку: что важно помнить о пользователе и о ваших разговорах "
                      "(факты, просьбы, предпочтения). До 10 коротких пунктов, по-русски.")
            ans = _try_llm(groq_client, FAST_MODEL, [{"role": "user", "content": prompt}],
                           max_tokens=500, temperature=0.2)
            if ans and ans.strip():
                with self._lock:
                    self.summary = ans.strip()[:2000]
                    self.messages = self.messages[len(old):]
                self.save()
                print("🧠 Память сжата.")
        finally:
            self._summarizing = False


MEMORY = Memory()


def _push_history(role, content):
    MEMORY.push(role, content)


# ==================== ОТВЕТЫ ====================
_EMOTION_HINT = (
    "В САМОМ НАЧАЛЕ ответа поставь один тег эмоции из списка: "
    + ", ".join(f"[{e}]" for e in EMOTIONS) + ". Тег не читается вслух и управляет лицом аватара. "
    "Выбирай по смыслу и по своему характеру: шутка, похвала, приветствие, игра → [happy]; "
    "хвастаешься знаниями или подкалываешь → [smug]; неожиданный вопрос → [surprised]; "
    "комплимент или неловкость → [shy]; грубость → [angry]; грустная тема → [sad]. "
    "[neutral] — только для сухих справок. "
)


def _sys_chat():
    s = (
        "Ты — Рокси Мигурдия. Умная, немного гордая аниме-помощница. "
        "Отвечай кратко, по делу, только на русском, без markdown. Умеешь создавать файлы, "
        "открывать программы, управлять окнами, работать с Telegram, таймерами и Vision-агентом. "
        "ВАЖНО: если просят прочитать/рассказать конкретное литературное произведение, "
        "стихотворение или сказку — НЕ выдумывай текст и НЕ пересказывай сюжет кратко. "
        "Если знаешь точный текст наизусть — прочитай его. Если не знаешь точный текст — "
        "честно скажи: «Я не помню точный текст наизусть, лучше добавить это произведение "
        "в библиотеку через build_library.py». " + _EMOTION_HINT
    )
    s += f"\n\nСейчас (по часам компьютера): {now_for_prompt()}. Дату и время берёшь отсюда, не выдумывай их."
    if MEMORY.summary:
        s += "\n\nЧто ты помнишь о пользователе и прошлых разговорах:\n" + MEMORY.summary
    return s


def _build_msgs(user_text, intent):
    if intent == "[CODE]":
        cur = "Ты — опытный программист. Пиши чистый код с комментариями."
    elif intent == "[TEXT]":
        cur = "Ты — талантливый писатель. Пиши грамотно и выразительно. " + _EMOTION_HINT
    else:
        cur = _sys_chat()
    return [{"role": "system", "content": cur}] + MEMORY.context() + [{"role": "user", "content": user_text}]


_SENT_END = re.compile(r"[.!?…]+[»\")]*(?=\s|$)|\n+")


def split_ready_sentences(buf, min_len=24):
    """Отрезает от начала буфера готовые предложения. → (список_предложений, остаток).
    Короткие куски («Да.», «Ну!») склеиваются со следующими — TTS лучше звучит фразами, а не словами.
    Тег эмоции в начале ([happy]) не режется."""
    out, start = [], 0
    for m in _SENT_END.finditer(buf):
        end = m.end()
        chunk = buf[start:end]
        if len(chunk.strip()) >= min_len or "\n" in m.group(0):
            if chunk.strip():
                out.append(chunk.strip())
            start = end
    return out, buf[start:]


def think_llm_stream(user_text, intent="[CHAT]", remember=True):
    """Как think_llm, но отдаёт ответ ПО ПРЕДЛОЖЕНИЯМ по мере генерации — Рокси начинает говорить с первого.
    Первое предложение может начинаться с тега эмоции. Поток не открылся — пробуем следующую модель;
    оборвался на середине — договариваем то, что успели получить."""
    msgs = _build_msgs(user_text, intent)
    full = ""
    for table in (ONLINE_MODELS, GROQ_FALLBACK_MODELS, LOCAL_MODELS):
        info = table.get(intent, table["[CHAT]"])
        if info["client"] is None:
            continue
        stream = _chat(info["client"], info["model"], msgs, stream=True)
        if stream is None:
            continue
        buf, got = "", False
        try:
            for ch in stream:
                d = ch.choices[0].delta.content if ch.choices and ch.choices[0].delta else None
                if not d:
                    continue
                got = True
                buf += d
                full += d
                ready, buf = split_ready_sentences(buf)
                for sent in ready:
                    yield sent
        except Exception as e:
            print(f"   ⚠ поток {info['model']}: {type(e).__name__}: {e}")
        if buf.strip():
            yield buf.strip()
        if got:
            break
    if not full.strip():
        yield "[sad] Извини, не смогла сформулировать."
        return
    print(f"🤖 {full[:80]}...")
    if remember:
        _, clean = extract_emotion(full)
        _push_history("user", user_text)
        _push_history("assistant", clean)


def think_llm(user_text, intent="[CHAT]", remember=True):
    """Возвращает ответ (может начинаться с тега эмоции)."""
    msgs = _build_msgs(user_text, intent)

    ans = None
    for table in (ONLINE_MODELS, GROQ_FALLBACK_MODELS, LOCAL_MODELS):
        info = table.get(intent, table["[CHAT]"])
        if info["client"] is None:
            continue
        ans = _try_llm(info["client"], info["model"], msgs)
        if ans:
            break
    if not ans:
        return "[sad] Извини, не смогла сформулировать."
    print(f"🤖 {ans[:80]}...")
    if remember:
        _, clean = extract_emotion(ans)
        _push_history("user", user_text)
        _push_history("assistant", clean)
    return ans


# ==================== КОД ====================
def _parse_code_answer(ans):
    if not ans:
        return None
    ans = ans.strip()
    m = re.match(r"CODE_TASK\s*:\s*(.+)", ans, re.IGNORECASE | re.DOTALL)
    if m: return m.group(1).strip()
    m = re.match(r"\[CODE\]\s*(.+?)\s*\[/CODE\]", ans, re.IGNORECASE | re.DOTALL)
    if m: return m.group(1).strip()
    m = re.match(r"\[CODE\]\s*(.+)", ans, re.IGNORECASE | re.DOTALL)
    if m: return m.group(1).strip().rstrip("]").strip()
    return None


def analyze_and_extract(user_text):
    print("🧠 Анализ запроса...")
    sys_prompt = ("Проанализируй фразу. Если пользователь просит НАПИСАТЬ/СГЕНЕРИРОВАТЬ/ВСТАВИТЬ КОД, "
                  "верни 'CODE_TASK: <чистая задача>'. Иначе верни 'CHAT'.")
    msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_text}]
    ans = _try_llm(groq_client, FAST_MODEL, msgs, max_tokens=300, temperature=0.0)
    if not ans or not ans.strip():
        ans = _try_llm(ollama_client, "qwen2.5:7b", msgs, max_tokens=150, temperature=0.0)
    task = _parse_code_answer(ans)
    if task:
        print(f"🎯 Задача: {task}")
        return "[ACTION_CODE_FILE]", task
    return "[CHAT]", ""


def generate_code(task_prompt):
    sys_prompt = "Напиши код на Python. Верни ТОЛЬКО чистый код, без markdown, без ```python. Код должен работать сразу."
    msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": task_prompt}]
    for client, model, label in ((groq_client, CODE_MODEL, "Groq"),
                                 (openrouter_client, "deepseek/deepseek-r1:free", "OpenRouter"),
                                 (ollama_client, "qwen2.5-coder:7b", "локально")):
        print(f"💻 Код: {label}...")
        ans = _try_llm(client, model, msgs, max_tokens=2500, temperature=0.2)
        if ans:
            return ans.strip()
    return None


def clean_code(code):
    if not code:
        return ""
    code = re.sub(r"^```\w*\s*$", "", code, flags=re.MULTILINE)
    return code.strip()


# ==================== TELEGRAM ====================
_REWRITE_PROMPT = """Ты помогаешь отправить сообщение в мессенджере контакту "{contact}".
Пользователь надиктовал сырую мысль: "{raw_text}"

Перепиши это как ГОТОВОЕ сообщение этому человеку от первого лица.

Правила:
- Заменяй третье лицо ("он", "она", имя) на второе ("ты", "тебя", "тебе").
- Убирай вводные обороты: "то что", "что", "мол", "типа".
- Сохрани смысл и интонацию (если мат — оставь мат).
- Не добавляй ничего от себя.
- Верни ТОЛЬКО текст сообщения."""


def rewrite_message_for_contact(contact, raw_text):
    if not raw_text or not raw_text.strip():
        return raw_text
    print(f"📝 Переформулирую для {contact}: '{raw_text[:80]}'")
    prompt = _REWRITE_PROMPT.format(contact=contact, raw_text=raw_text.strip())
    ans = _try_llm(groq_client, FAST_MODEL, [{"role": "system", "content": "Ты переписываешь сообщения."},
                                             {"role": "user", "content": prompt}],
                   max_tokens=400, temperature=0.4)
    result = (ans or "").strip().strip("\"'`«»").strip()
    return result or raw_text


def draft_tg_reply(history, instruction):
    prompt = (f"История диалога в Telegram:\n\n{history}\n\nПользователь дал команду: «{instruction}». "
              "Составь ответ от лица пользователя. Верни ТОЛЬКО текст ответа.")
    ans = _try_llm(groq_client, FAST_MODEL, [{"role": "system", "content": "Ты помогаешь написать ответ в мессенджере. Отвечай кратко."},
                                             {"role": "user", "content": prompt}], max_tokens=400, temperature=0.7)
    return (ans or "").strip() or None


# ==================== TOOL-CALLING РОУТЕР ====================
# LLM не выполняет действия сама: она переводит свободную фразу в каноническую команду,
# которую дальше разбирают те же регулярки. Так у роутера нет «своих» опасных путей.
def _tool(name, desc, props, required=()):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(required)}}}


_S = {"type": "string"}
ROUTER_TOOLS = [
    _tool("open_target", "Открыть программу, сайт, файл или папку", {"target": _S}, ["target"]),
    _tool("weather", "Узнать погоду", {"city": _S}),
    _tool("create_item", "Создать файл или папку", {"name": _S, "is_folder": {"type": "boolean"},
                                                    "in_current_folder": {"type": "boolean"}}, ["name"]),
    _tool("window_action", "Свернуть/развернуть/закрыть/активировать окно",
          {"action": {"type": "string", "enum": ["сверни", "разверни", "закрой", "активируй"]}, "window": _S}, ["action"]),
    _tool("type_text", "Напечатать текст в Блокнот", {"text": _S}, ["text"]),
    _tool("write_code", "Написать программу/скрипт на Python", {"task": _S}, ["task"]),
    _tool("telegram_send", "Отправить сообщение в Telegram", {"contact": _S, "text": _S}, ["contact", "text"]),
    _tool("telegram_read", "Прочитать последние сообщения в Telegram", {"contact": _S}, ["contact"]),
    _tool("set_timer", "Поставить таймер", {"minutes": {"type": "number"}}, ["minutes"]),
    _tool("remind", "Напомнить о чём-то через время", {"minutes": {"type": "number"}, "what": _S}, ["minutes", "what"]),
    _tool("media", "Управление звуком и плеером. Для точной громкости — action «громкость» и level 0–100",
          {"action": {"type": "string", "enum": ["громче", "тише", "громкость", "выключи звук", "включи звук", "пауза",
                                                 "следующий трек", "предыдущий трек", "что играет"]},
           "level": {"type": "number"}},
          ["action"]),
    _tool("look_at_screen", "Посмотреть на экран и ответить на вопрос о нём", {}),
]

_ROUTER_SYSTEM = (
    "Ты маршрутизатор голосовых команд ассистента на Windows. Если пользователь просит выполнить действие "
    "на компьютере — вызови подходящий инструмент. Если он просто разговаривает, задаёт вопрос или просит "
    "что-то рассказать — НЕ вызывай инструменты и ответь одним словом: TEXT, если просят написать "
    "художественный текст (стих, рассказ, сочинение), иначе CHAT."
)


def _canonical(name, a):
    g = lambda k: str(a.get(k) or "").strip()
    if name == "open_target": return f"открой {g('target')}"
    if name == "weather": return f"погода в {g('city')}" if g("city") else "погода"
    if name == "create_item":
        kind = "папку" if a.get("is_folder") else "файл"
        return f"создай {kind} с именем {g('name')}" + (" тут" if a.get("in_current_folder") else "")
    if name == "window_action": return f"{g('action')} {g('window')}".strip()
    if name == "type_text": return f"напечатай {g('text')}"
    if name == "write_code": return f"напиши код {g('task')}"
    if name == "telegram_send": return f"напиши в телеграм {g('contact')} {g('text')}"
    if name == "telegram_read": return f"прочитай телеграм {g('contact')}"
    if name == "set_timer": return f"поставь таймер на {g('minutes')} минут"
    if name == "remind": return f"напомни через {g('minutes')} минут {g('what')}"
    if name == "media":
        if g("action") == "громкость" and g("level"): return f"громкость {g('level').split('.')[0]}"
        return g("action")
    if name == "look_at_screen": return "что на экране"   # роутер: [ACTION_SCREEN] с вопросом «что на экране»
    return None


def llm_route(user_text):
    """→ (intent, task) или None (= обычный чат)."""
    import intents
    r = _chat(groq_client, ROUTER_MODEL,
              [{"role": "system", "content": _ROUTER_SYSTEM}, {"role": "user", "content": user_text}],
              max_tokens=300, temperature=0.0, tools=ROUTER_TOOLS, tool_choice="auto")
    if r is not None and r.choices:
        msg = r.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if calls:
            fn = calls[0].function
            try:
                args = json.loads(fn.arguments or "{}")
            except Exception:
                args = {}
            phrase = _canonical(fn.name, args)
            print(f"🧭 Роутер: {fn.name} → «{phrase}»")
            if phrase:
                intent, task = intents.get_intent(phrase, code_analyzer=None, llm_router=None)
                if intent == "[ACTION_CODE_FILE]" and not task:
                    task = str(args.get("task") or "")
                if intent not in ("[CHAT]", "[TEXT]"):
                    return intent, task
            return None
        if "TEXT" in (msg.content or "").upper():
            return "[TEXT]", ""
        return None
    # запасной вариант — старый локальный роутер
    ans = _try_llm(ollama_client, "qwen2.5:0.5b", [{"role": "system", "content": "Ответь одной командой: [TEXT] или [CHAT]"},
                                                    {"role": "user", "content": user_text}], max_tokens=10, temperature=0.0)
    if ans and "[TEXT]" in ans.upper():
        return "[TEXT]", ""
    return None
