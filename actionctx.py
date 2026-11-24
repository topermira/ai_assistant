"""Контекст выполнения действий: подтверждения и таймеры. Отдельный модуль без тяжёлых импортов (для тестов)."""


class ActionContext:
    def __init__(self, config, confirm=None, timers=None, remote=None):
        self.config = config
        self._confirm = confirm
        self.timers = timers
        self._remote = remote        # () -> (send_file | None, команда_из_чата: bool)

    def from_chat(self):
        """Команда пришла с телефона (из чата управления), а не голосом."""
        try:
            return bool(self._remote and self._remote()[1])
        except Exception:
            return False

    def can_send_files(self):
        try:
            return bool(self._remote and self._remote()[0])
        except Exception:
            return False

    def send_file(self, data, name, caption="", photo=False):
        """Отправить в чат управления на телефоне. data — bytes или путь. → True, если ушло."""
        try:
            fn = self._remote and self._remote()[0]
        except Exception:
            fn = None
        if not fn:
            return False
        try:
            fn(data, name, caption, photo)
            return True
        except Exception as e:
            print(f"⚠️ Отправка в Telegram: {type(e).__name__}: {e}")
            return False

    def confirm(self, question, force=False):
        """«Да/нет» перед опасным действием.

        • Нет функции подтверждения (контекст создан без неё) → ОТКАЗ. Раньше было «да» — fail-open.
        • confirm_dangerous=false отключает вопросы, КРОМЕ force=True: Telegram-отправки и отправка экрана
          в облако спрашивают всегда, потому что за ними стоит чужой текст или приватные данные."""
        if self._confirm is None:
            return False
        if not force and not self.config.get("confirm_dangerous", True):
            return True
        return self._confirm(question)
