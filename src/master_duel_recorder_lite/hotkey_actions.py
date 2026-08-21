from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum


class HotkeyCommand(str, Enum):
    TOGGLE_RECORDING = "toggle_recording"
    ADD_MARKER = "add_marker"
    TOGGLE_WATCH = "toggle_watch"
    SHOW_STATUS = "show_status"


class OperationState(str, Enum):
    STOPPED = "stopped"
    RECORDING = "recording"
    WATCHING = "watching"
    BUSY = "busy"


@dataclass(frozen=True)
class HotkeyActionResult:
    accepted: bool
    command: HotkeyCommand
    message: str


Handler = Callable[[], str]


ALLOWED_COMMANDS: Mapping[OperationState, frozenset[HotkeyCommand]] = {
    OperationState.STOPPED: frozenset(
        {
            HotkeyCommand.TOGGLE_RECORDING,
            HotkeyCommand.TOGGLE_WATCH,
            HotkeyCommand.SHOW_STATUS,
        }
    ),
    OperationState.RECORDING: frozenset(
        {
            HotkeyCommand.TOGGLE_RECORDING,
            HotkeyCommand.ADD_MARKER,
            HotkeyCommand.SHOW_STATUS,
        }
    ),
    OperationState.WATCHING: frozenset(
        {
            HotkeyCommand.TOGGLE_WATCH,
            HotkeyCommand.ADD_MARKER,
            HotkeyCommand.SHOW_STATUS,
        }
    ),
    OperationState.BUSY: frozenset({HotkeyCommand.SHOW_STATUS}),
}


class HotkeyDispatcher:
    def __init__(self, handlers: Mapping[HotkeyCommand, Handler]) -> None:
        self.handlers = dict(handlers)

    def dispatch(
        self, command: HotkeyCommand, *, state: OperationState
    ) -> HotkeyActionResult:
        if command not in ALLOWED_COMMANDS[state]:
            return HotkeyActionResult(
                False,
                command,
                f"{state.value}中は{command.value}を実行できません",
            )
        handler = self.handlers.get(command)
        if handler is None:
            return HotkeyActionResult(False, command, "操作ハンドラが未接続です")
        return HotkeyActionResult(True, command, handler())


def default_hotkey_bindings(
    *,
    record_toggle: str,
    marker: str,
    watch_toggle: str,
) -> dict[str, HotkeyCommand]:
    bindings = (
        (record_toggle, HotkeyCommand.TOGGLE_RECORDING),
        (marker, HotkeyCommand.ADD_MARKER),
        (watch_toggle, HotkeyCommand.TOGGLE_WATCH),
    )
    normalized: dict[str, HotkeyCommand] = {}
    for key, command in bindings:
        value = key.strip()
        if not value:
            raise ValueError("hotkeyは空にできません")
        if value in normalized:
            raise ValueError(f"hotkeyが重複しています: {value}")
        normalized[value] = command
    return normalized
