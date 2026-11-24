"""Управление с телефона: клавиши, сетка, зажатие мыши, система, файлы/буфер."""
import io

import keys
import phonefiles
import pcctl
import screengrid
import syscmd
from actionctx import ActionContext
from intents import get_intent


def test_keys():
    assert keys.parse_keys("нажми enter") == ("enter", 1)
    assert keys.parse_keys("enter") == ("enter", 1)
    assert keys.parse_keys("нажми контрол си") == ("ctrl+c", 1)
    assert keys.parse_keys("нажми alt tab") == ("alt+tab", 1)
    assert keys.parse_keys("нажми эф пять") == ("f5", 1)
    assert keys.parse_keys("нажми стрелку вниз три раза") == ("down", 3)
    assert keys.parse_keys("нажми ctrl+с") == ("ctrl+c", 1)            # кириллическая «с»
    assert keys.parse_keys("нажми на кнопку отправить") is None        # → агент по экрану
    assert keys.parse_keys("вниз") is None and keys.parse_keys("шифт") is None
    assert get_intent("нажми enter") == ("[ACTION_KEYS]", "enter|1")
    assert get_intent("нажми правой кнопкой мыши")[0] == "[ACTION_MOUSE]"


def test_grid_cells():
    assert screengrid.find_cell("б4")[:2] == (1, 3)
    assert screengrid.find_cell("бэ 4")[:2] == (1, 3)
    assert screengrid.find_cell("b4")[:2] == (2, 3)                     # латинская B похожа на В
    assert screengrid.find_cell("т9")[:2] == (15, 8)
    assert screengrid.find_cell("з4") is None                           # З нет: на снимке путается с 3
    assert screengrid.find_cell("а10") is None                          # строк 9
    assert screengrid.cell_center(0, 0, 1600, 900) == (50, 50)
    assert screengrid.cell_of(1599, 899, 1600, 900) == (15, 8)
    assert get_intent("кликни Б4") == ("[ACTION_MOUSE]", "cell|1|3|left|1")
    assert get_intent("курсор в бэ четыре") == ("[ACTION_MOUSE]", "cell|1|3||0")
    assert get_intent("нажми правой в Б4") == ("[ACTION_MOUSE]", "cell|1|3|right|1")
    assert get_intent("громкость 30")[0] != "[ACTION_MOUSE]"


def test_grid_render():
    from PIL import Image
    data = screengrid.render(Image.new("RGB", (2560, 1440), (40, 60, 90)), cursor=(1280, 720))
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG" and im.size == (1600, 900)


def test_hold_release():
    assert pcctl.parse_mouse("зажми левую кнопку мыши") == ("down", "left")
    assert pcctl.parse_mouse("отпусти") == ("up", "all")
    assert pcctl.parse_mouse("отпусти правую") == ("up", "right")


def test_syscmd():
    assert syscmd.parse("заблокируй компьютер") == ("lock",)
    assert syscmd.parse("переведи компьютер в спящий режим") == ("sleep",)
    assert syscmd.parse("как дела у компа") == ("status",)
    assert syscmd.parse("что запущено") == ("procs",)
    assert syscmd.parse("заверши хром") == ("kill", "хром")
    assert syscmd.parse("заряд телефона") is None
    assert syscmd.parse("как дела") is None
    assert syscmd.resolve_proc("хром") == "chrome.exe" and syscmd.resolve_proc("кракозябра") is None
    assert "explorer.exe" in syscmd.PROTECTED
    assert get_intent("заблокируй компьютер") == ("[ACTION_SYS]", "lock")


def test_phonefiles():
    p = phonefiles.parse
    assert p("пришли файл отчёт.docx") == ("file", "отчёт.docx")
    assert p("скинь мне файл отчёт.docx на телефон") == ("file", "отчёт.docx")
    assert p("пришли скриншот") == ("screen",)
    assert p("скинь скриншот Васе") is None
    assert p("пришли скрин с сеткой") == ("grid",) and p("/grid") == ("grid",)
    assert p("что в буфере") == ("clip_get",)
    assert p("положи в буфер: Привет Мир") == ("clip_set", "Привет Мир")
    assert p("скопируй") is None


def test_secret_files_never_sent():
    assert phonefiles.is_secret("C:/p/.env")
    assert phonefiles.is_secret("C:/p/roxy_session.session")
    assert phonefiles.is_secret("C:/p/config.json", "C:/p")
    assert phonefiles.is_secret("C:/Users/u/.ssh/known_hosts")
    assert not phonefiles.is_secret("C:/Users/u/Desktop/config.json", "C:/p")
    assert not phonefiles.is_secret("C:/Users/u/Desktop/отчёт.docx")


def test_unique_path(tmp_path):
    (tmp_path / "фото.jpg").write_bytes(b"x")
    assert phonefiles.unique_path(str(tmp_path), "фото.jpg").endswith("фото (1).jpg")


def test_context_remote():
    sent = []
    ctx = ActionContext({}, remote=lambda: (lambda d, n, c, ph: sent.append((n, c, ph)), True))
    assert ctx.from_chat() and ctx.can_send_files()
    assert ctx.send_file(b"x", "a.jpg", "cap", photo=True) and sent == [("a.jpg", "cap", True)]
    voice = ActionContext({}, remote=lambda: (None, False))
    assert not voice.from_chat() and not voice.send_file(b"x", "a")
    assert not ActionContext({}).from_chat()
