"""Скриншоты, OCR, поиск элемента на экране, Vision-агент."""
import io
import re
import json
import time
import base64

import mss
import pyautogui
import kbfix  # noqa: F401 — Ctrl+V/C/N… работают и в русской раскладке
import pytesseract
import pygetwindow as gw
from PIL import Image

from settings import TESSERACT_PATH, VISION_MODELS, config
from llm import groq_client, _try_llm, think_llm, _sys_chat, _push_history, MEMORY
from text_tts import extract_emotion
from textinput import type_text
from agent_guard import validate_action, is_system_process

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
pyautogui.FAILSAFE = True   # увести мышь в левый верхний угол = аварийная остановка агента

_OWN_WINDOWS = ("Roxy Console", "Roxy Live2D", "Roxy Input")
MAX_SHOT_WIDTH = 1400

def _mss_grab(bbox=None):
    try:
        with mss.MSS() as sct:
            return sct.grab(bbox) if bbox else sct.grab(sct.monitors[1])
    except AttributeError:
        with mss.mss() as sct:
            return sct.grab(bbox) if bbox else sct.grab(sct.monitors[1])


def screenshot_active_window_ex():
    """→ (img, left, top, scale). Координаты на картинке переводятся в экранные так:
    x_screen = left + x_img / scale."""
    win = gw.getActiveWindow()
    bbox = None
    if win and (win.title or "").strip() and win.title not in _OWN_WINDOWS:
        try:
            bbox = {"top": max(0, win.top), "left": max(0, win.left),
                    "width": max(10, win.width), "height": max(10, win.height)}
        except Exception:
            bbox = None
    left, top = (bbox["left"], bbox["top"]) if bbox else (0, 0)
    try:
        shot = _mss_grab(bbox)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if not bbox:
            left, top = shot.left, shot.top
    except Exception as e:
        print(f"⚠️ screenshot (mss): {e}")
        try:
            img = pyautogui.screenshot(region=(bbox["left"], bbox["top"], bbox["width"], bbox["height"])) if bbox else pyautogui.screenshot()
        except Exception as e2:
            print(f"⚠️ screenshot (pyautogui): {e2}")
            return None, 0, 0, 1.0
    scale = 1.0
    if img.width > MAX_SHOT_WIDTH:
        scale = MAX_SHOT_WIDTH / img.width
        img = img.resize((MAX_SHOT_WIDTH, int(img.height * scale)))
    return img, left, top, scale


def _screenshot_active_window():
    return screenshot_active_window_ex()[0]


def _img_b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _vision_find_target(query, context="файл"):
    if not groq_client: return None
    img = _screenshot_active_window()
    if not img: return None
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        print(f"⚠️ base64: {e}")
        return None
    prompt = (
        f"Пользователь ищет {context} с именем или похожим на '{query}'. "
        f"Найди среди видимых элементов самый похожий. Верни ТОЛЬКО точное имя. "
        f"Если ничего похожего нет — верни NOT_FOUND."
    )
    for model in VISION_MODELS:
        try:
            print(f"👁️ Vision: пробую {model}...")
            kwargs = dict(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]
                }],
                temperature=0.0, max_tokens=60,
            )
            if "qwen" in model.lower(): kwargs["extra_body"] = {"reasoning_effort": "none"}
            response = groq_client.chat.completions.create(**kwargs)
            ans = (response.choices[0].message.content or "").strip().strip('"\'`«» ')
            if ans and "NOT_FOUND" not in ans.upper() and len(ans) < 200 and "\n" not in ans:
                print(f"👁️ Vision ({model}): '{ans}'")
                return ans
        except Exception as e:
            print(f"⚠️ Vision {model}: {type(e).__name__}: {e}")
            continue
    return None

def _ocr_find_target(query, context="файл"):
    img = _screenshot_active_window()
    if not img: return None
    try: extracted = pytesseract.image_to_string(img, lang="rus+eng")
    except Exception: return None
    if not extracted.strip(): return None
    prompt = (
        f"OCR-текст:\n\n{extracted}\n\n"
        f"Пользователь ищет {context}, похожий на '{query}'. Верни ТОЛЬКО имя или NOT_FOUND."
    )
    ans = _try_llm(groq_client, "openai/gpt-oss-20b", [
        {"role": "system", "content": "Отвечай только именем или NOT_FOUND."},
        {"role": "user", "content": prompt},
    ], max_tokens=30, temperature=0.0)
    if ans:
        ans = ans.strip().strip('"\'`«» ')
        if ans and "NOT_FOUND" not in ans.upper() and len(ans) < 200 and "\n" not in ans:
            return ans
    return None

def _smart_find_target(query, context="файл"):
    return _vision_find_target(query, context) or _ocr_find_target(query, context)


_SCREEN_HINT = (
    "Сейчас пользователь показывает тебе свой экран (скриншот активного окна) и спрашивает о нём. "
    "Ответь по-русски, коротко и по делу, 1–4 предложения: ответ звучит голосом, поэтому без списков, кода, "
    "ссылок и markdown. Если на экране ошибка — скажи, в чём она и как её исправить. Если просят мнение — "
    "дай его своими словами, в своём характере. Не выдумывай того, чего на скриншоте не видно."
)


def _vision_answer(question, img):
    """Ответ мультимодальной модели по картинке экрана (или None)."""
    if not groq_client:
        return None
    try:
        b64 = _img_b64(img)
    except Exception as e:
        print(f"⚠️ base64: {e}")
        return None
    sys_msg = _sys_chat() + "\n\n" + _SCREEN_HINT
    for model in VISION_MODELS:
        try:
            print(f"👁️ Экран: пробую {model}...")
            kwargs = dict(
                model=model,
                messages=[{"role": "system", "content": sys_msg}] + MEMORY.context()[-6:] + [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                temperature=0.5, max_tokens=300,
            )
            if "qwen" in model.lower(): kwargs["extra_body"] = {"reasoning_effort": "none"}
            r = groq_client.chat.completions.create(**kwargs)
            ans = (r.choices[0].message.content or "").strip()
            if ans:
                return ans
        except Exception as e:
            print(f"⚠️ Экран {model}: {type(e).__name__}: {e}")
    return None


def read_screen_and_analyze(prompt_text):
    """«Рокси, что за ошибка?», «глянь на экран, что думаешь?» — скриншот активного окна (или всего экрана,
    если активно окно самой Рокси) → модель с картинкой; если она недоступна — текст с экрана (OCR) → обычная LLM."""
    question = (prompt_text or "").strip() or "Что на экране?"
    print("📸 Скриншот...")
    try:
        img = _screenshot_active_window()
        if img is None:
            return "Не удалось сделать скриншот."
        ans = _vision_answer(question, img)
        if ans:
            print(f"🤖 {ans[:80]}...")
            _push_history("user", f"(показал экран) {question}")
            _push_history("assistant", extract_emotion(ans)[1])
            return ans
        text = pytesseract.image_to_string(img, lang="rus+eng")
        if not text.strip():
            return "На экране не вижу текста, а картинку сейчас разобрать не могу."
        return think_llm(f"{question}\n\nТекст с экрана (распознан с картинки, может быть с ошибками):\n{text[:6000]}", "[CHAT]")
    except Exception as e:
        return f"Ошибка: {e}"


# ==================== VISION AGENT ====================
# Раньше промпт форматировался через .format(), а в нём JSON с фигурными скобками → KeyError,
# и агент НИ РАЗУ не доходил до модели. Теперь подстановка через replace.
VISION_AGENT_PROMPT = """Ты — агент по управлению компьютером. Дан скриншот окна размером {width}x{height} пикселей и задача.
Верни РОВНО ОДИН JSON-объект с действием. Координаты — в пикселях ЭТОГО скриншота. Не добавляй пояснений.

Действия:
- {"action": "click", "x": 100, "y": 200, "button": "left"}
- {"action": "double_click", "x": 100, "y": 200}
- {"action": "right_click", "x": 100, "y": 200}
- {"action": "type", "text": "текст"}
- {"action": "press", "key": "enter"}
- {"action": "hotkey", "keys": ["ctrl", "s"]}
- {"action": "scroll", "clicks": 3, "x": 100, "y": 200}
- {"action": "wait", "seconds": 2}
- {"action": "done", "message": "Готово"}
- {"action": "fail", "reason": "Причина"}

Задача: {instruction}
Уже сделано: {history}

Если задача уже выполнена или это невозможно — верни done или fail. Отвечай ТОЛЬКО JSON."""


def _build_agent_prompt(instruction, width, height, history):
    return (VISION_AGENT_PROMPT
            .replace("{instruction}", instruction)
            .replace("{width}", str(width)).replace("{height}", str(height))
            .replace("{history}", history or "ничего"))


def _parse_action_json(raw):
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0) if m else raw)


def _vision_agent_step(instruction, b64, width, height, history):
    prompt = _build_agent_prompt(instruction, width, height, history)
    for model in VISION_MODELS:
        raw = ""
        try:
            print(f"👁️ Vision Agent: {model}...")
            kwargs = dict(
                model=model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}],
                temperature=0.0, max_tokens=200,
            )
            if "qwen" in model.lower():
                kwargs["extra_body"] = {"reasoning_effort": "none"}
            response = groq_client.chat.completions.create(**kwargs)
            raw = response.choices[0].message.content or ""
            return _parse_action_json(raw)
        except json.JSONDecodeError as e:
            print(f"⚠️ Vision JSON parse: {e} | raw: {raw[:200]}")
        except Exception as e:
            print(f"⚠️ Vision {model}: {type(e).__name__}: {e}")
    return {"action": "fail", "reason": "все vision-модели упали"}


def _to_screen(action, left, top, scale):
    """Координаты скриншота → экран."""
    if "x" in action and action.get("x") is not None:
        action["x"] = int(left + float(action["x"]) / scale)
    if "y" in action and action.get("y") is not None:
        action["y"] = int(top + float(action["y"]) / scale)
    return action


def _active_process_name():
    """Имя процесса активного окна (chrome.exe) или None, если узнать нельзя (нет pywin32/psutil)."""
    try:
        import win32gui
        import win32process
        import psutil
        _, pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
        return psutil.Process(pid).name()
    except Exception:
        return None


def _vision_execute_action(action):
    act = action.get("action")
    try:
        if act == "click": pyautogui.click(action["x"], action["y"], button=action.get("button", "left"))
        elif act == "double_click": pyautogui.doubleClick(action["x"], action["y"])
        elif act == "right_click": pyautogui.rightClick(action["x"], action["y"])
        elif act == "type": type_text(action["text"])      # кириллица через буфер (pyautogui.write её не вводит)
        elif act == "press": pyautogui.press(action["key"])
        elif act == "hotkey": pyautogui.hotkey(*action["keys"])
        elif act == "scroll": pyautogui.scroll(int(action["clicks"]), x=action.get("x"), y=action.get("y"))
        elif act == "wait": time.sleep(min(10, float(action.get("seconds", 1))))
        elif act in ("done", "fail"): return act
        else:
            print(f"⚠️ Неизвестное действие: {act}")
            return "fail"
        return "ok"
    except pyautogui.FailSafeException:
        print("🛑 FAILSAFE: мышь в углу — агент остановлен.")
        return "fail"
    except Exception as e:
        print(f"❌ execute_action: {e}")
        return "fail"


def execute_vision_task(instruction, max_steps=10, should_stop=None):
    print(f"🤖 Vision Agent: задача '{instruction}' (макс {max_steps} шагов)")
    history = []
    for step in range(1, max_steps + 1):
        if should_stop and should_stop():
            return "Остановила агента."
        img, left, top, scale = screenshot_active_window_ex()
        if img is None:
            return "Не удалось сделать скриншот."
        try:
            b64 = _img_b64(img)
        except Exception as e:
            return f"Ошибка кодирования: {e}"
        action = _vision_agent_step(instruction, b64, img.width, img.height, "; ".join(history[-5:]))
        print(f"   Шаг {step}: {action}")
        if action.get("action") == "done": return f"Готово. {action.get('message', '')}"
        if action.get("action") == "fail": return f"Не смогла: {action.get('reason', '')}"
        ok, why = validate_action(action)
        if not ok:
            print(f"🛡️ Агент: действие отклонено ({why})")
            return f"Остановила агента: действие запрещено ({why})."
        proc = _active_process_name()
        if is_system_process(proc) and not config.get("vision_allow_system_apps", False):
            print(f"🛡️ Агент: на переднем плане системная программа {proc}")
            return f"Остановила агента: на экране системная программа ({proc}). Такое ему без отдельного разрешения нельзя."
        action = _to_screen(dict(action), left, top, scale)
        if _vision_execute_action(action) == "fail":
            return "Ошибка выполнения действия."
        history.append(json.dumps({k: v for k, v in action.items() if k != "text"}, ensure_ascii=False))
        time.sleep(0.6)
    return f"Превышен лимит {max_steps} шагов."
