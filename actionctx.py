"""Контекст выполнения действий: подтверждения и таймеры. Отдельный модуль без тяжёлых импортов (для тестов)."""


class ActionContext:
    def __init__(self, config, confirm=None, timers=None):
        self.config = config
        self._confirm = confirm
        self.timers = timers

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
