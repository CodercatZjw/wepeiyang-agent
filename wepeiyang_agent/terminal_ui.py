"""Keyboard-driven inline pickers, with a line-based fallback for redirected input."""
from __future__ import annotations

import getpass
import sys

from prompt_toolkit import Application, PromptSession, prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, Label, TextArea


COMMANDS = [("/model", "切换当前 Provider 的模型"), ("/provider", "选择或添加 Provider")]
STYLE = Style.from_dict({"frame.border": "#d7875f", "frame.label": "#ffff00 bold",
                         "selected": "#ffff00 bold", "muted": "#888888", "item": "#d7af00"})


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        value = document.text_before_cursor
        if value.startswith("/") and " " not in value:
            for command, description in COMMANDS:
                if command.startswith(value) and command != value:
                    yield Completion(command, start_position=-len(value), display_meta=description)


class PickerState:
    def __init__(self, choices, default=None):
        self.choices = list(choices)
        self.default = default
        self.filtered = list(choices)
        self.index = next((i for i, (key, _) in enumerate(choices) if key == default), 0)

    def filter(self, query):
        self.filtered = [(key, label) for key, label in self.choices if query.casefold() in label.casefold()]
        self.index = next((i for i, (key, _) in enumerate(self.filtered) if key == self.default), 0)

    def move(self, delta):
        self.index = (self.index + delta) % (len(self.filtered) + 1)

    def selected(self):
        return self.filtered[self.index][0] if self.index < len(self.filtered) else None


def picker_application(title, choices, default=None, subtitle="", input=None, output=None):
    state = PickerState(choices, default)
    search = TextArea(height=1, prompt="筛选 > ", multiline=False)
    search.buffer.on_text_changed += lambda buffer: state.filter(buffer.text)

    def rows():
        items = [*state.filtered, (None, "取消 / Cancel")]
        start = max(0, min(state.index - 6, len(items) - 13))
        fragments = []
        for index in range(start, min(start + 13, len(items))):
            chosen = index == state.index
            fragments.append(("class:selected" if chosen else "class:item",
                              ("❯ " if chosen else "  ") + items[index][1] + "\n"))
        fragments.append(("class:muted", f"{len(state.filtered)} 项匹配 · ↑/↓ 选择 · 输入筛选 · Enter 确认 · Esc 取消"))
        return fragments

    keys = KeyBindings()
    @keys.add("up", eager=True)
    def up(event):
        state.move(-1)
    @keys.add("down", eager=True)
    def down(event):
        state.move(1)
    @keys.add("enter", eager=True)
    def accept(event):
        event.app.exit(result=state.selected())
    @keys.add("escape", eager=True)
    @keys.add("c-c", eager=True)
    @keys.add("c-d", eager=True)
    def cancel(event):
        event.app.exit(result=None)
    root = Frame(HSplit([Label(subtitle), search, Window(FormattedTextControl(rows), height=15)]), title=title)
    app = Application(layout=Layout(root, focused_element=search), key_bindings=keys, style=STYLE,
                      full_screen=False, mouse_support=False, input=input, output=output)
    return app


class TerminalUI:
    def __init__(self, interactive=None):
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self.session = PromptSession(completer=SlashCompleter(), complete_while_typing=True) if self.interactive else None

    def instruction(self):
        return self.session.prompt("\n你 > ") if self.session else input("\n你 > ")

    def message(self, text):
        print(text, flush=True)

    def text(self, label, default="", secret=False):
        if self.interactive:
            return prompt(label + " > ", default=default, is_password=secret)
        if secret:
            if not sys.stdin.isatty():
                raise ValueError("添加 Provider 的 API Key 需要在交互终端中隐藏输入。")
            return getpass.getpass(label + " > ")
        result = input(label + (f" [{default}]" if default else "") + " > ")
        return result or default

    def choose(self, title, choices, default=None, subtitle=""):
        if self.interactive:
            return picker_application(title, choices, default, subtitle).run()
        state = PickerState(choices, default)
        while True:
            self.message(f"\n── {title} ──\n{subtitle}")
            for index, (key, label) in enumerate(state.filtered):
                self.message(f"{'>' if index == state.index else ' '} {index + 1}. {label}")
            self.message("  0. 取消（输入编号或文字筛选，Enter 确认默认项）")
            value = input("选择 > ").strip()
            if value == "0":
                return None
            if not value:
                return state.selected()
            if value.isdigit():
                index = int(value) - 1
                if 0 <= index < len(state.filtered):
                    return state.filtered[index][0]
            else:
                state.filter(value)
