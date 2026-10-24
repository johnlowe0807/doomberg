"""Shared keyboard-only confirmation dialogs."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("n,N,escape", "dismiss(False)", show=False, priority=True)]
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; background: #000000 70%; }
    ConfirmScreen > Vertical { width: 76; height: auto; max-height: 90%; border: solid #FF5FD2;
        background: #000000; padding: 1 2; }
    ConfirmScreen #confirm-keys { margin-top: 1; color: #FF5FD2; }
    """

    def __init__(self, question: str, controls: str, detail: str = "") -> None:
        super().__init__()
        self.question, self.controls, self.detail = question, controls, detail

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.question, markup=False)
            if self.detail:
                yield Static(self.detail, markup=False)
            yield Static(self.controls, id="confirm-keys", markup=False)


class QuitConfirm(ConfirmScreen):
    def __init__(self) -> None:
        super().__init__("Quit freeterm?", "Ctrl+Q  Quit     N / Esc  Cancel")
