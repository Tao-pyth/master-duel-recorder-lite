"""録画を読み取り、対象カードの表示とログの使用候補を分けて抽出する。"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import unicodedata

from .card_ocr import CardAnalysisError, OcrLine, create_ocr_backend


ANALYZER_VERSION = "rapidocr-ppocrv6-v1"
MIN_SAMPLE_FPS = 0.25
MAX_SAMPLE_FPS = 2.0
DETAIL_ROI = (0.008, 0.127, 0.158, 0.187)
LOG_ROI = (0.862, 0.130, 0.990, 0.905)
ACTION_NAMES = {
    "発動": "activate", "通常召喚": "normal_summon", "特殊召喚": "special_summon",
    "融合召喚": "special_summon", "シンクロ召喚": "special_summon",
    "エクシーズ召喚": "special_summon", "リンク召喚": "special_summon", "召喚": "normal_summon",
}
ProgressCallback = Callable[[dict[str, object]], None]


def normalize_card_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold().strip("「」『』《》〈〉\"'")


def normalize_action_text(value: str) -> str:
    # 汎用多言語OCRの「召唤」表記を、ログ操作の照合時だけ日本語表記へ揃える。
    return normalize_card_text(value).replace("唤", "喚")


@dataclass(frozen=True)
class Target:
    target_id: str
    name: str
    names: tuple[str, ...]


def parse_targets(values: object) -> tuple[Target, ...]:
    if not isinstance(values, list):
        raise CardAnalysisError("invalid_request", "対象カードsnapshotは配列で指定してください")
    targets, ids = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise CardAnalysisError("invalid_request", "対象カードsnapshotの形式が不正です")
        if value.get("enabled", True) is False:
            continue
        target_id, name, aliases = value.get("target_id", value.get("id")), value.get("name"), value.get("aliases", [])
        if not isinstance(target_id, str) or not target_id.strip() or len(target_id.strip()) > 100:
            raise CardAnalysisError("invalid_request", "対象カードIDが不正または重複しています")
        target_id = target_id.strip()
        if target_id in ids:
            raise CardAnalysisError("invalid_request", "対象カードIDが不正または重複しています")
        if not isinstance(name, str) or not normalize_card_text(name):
            raise CardAnalysisError("invalid_request", "対象カード名が空または不正です")
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise CardAnalysisError("invalid_request", "対象カード別名の形式が不正です")
        names = tuple(dict.fromkeys(normalize_card_text(item) for item in (name, *aliases) if normalize_card_text(item)))
        targets.append(Target(target_id, name.strip(), names))
        ids.add(target_id)
    if not targets:
        raise CardAnalysisError("no_targets", "有効な対象カードがありません")
    return tuple(targets)


def match_target(text: str, targets: tuple[Target, ...]) -> tuple[Target, float] | None:
    normalized = normalize_card_text(text)
    matches = []
    for target in targets:
        best = 0.0
        for name in target.names:
            if normalized == name:
                best = 1.0
            elif len(name) >= 4 and len(normalized) >= 4:
                best = max(best, SequenceMatcher(None, normalized, name).ratio())
        if best >= 0.88:
            matches.append((target, best))
    matches.sort(key=lambda pair: pair[1], reverse=True)
    if not matches or len(matches) > 1 and matches[0][1] - matches[1][1] < 0.05:
        return None
    return matches[0]


def join_spatial_lines(lines: list[OcrLine]) -> list[OcrLine]:
    groups: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda row: (row.center_y, row.left)):
        if not normalize_card_text(line.text):
            continue
        group = next((group for group in groups if abs(group[0].center_y - line.center_y)
                      <= max(2.0, min(group[0].height, line.height) * 0.45)), None)
        if group is None:
            groups.append([line])
        else:
            group.append(line)
    merged = []
    for group in groups:
        ordered = sorted(group, key=lambda row: row.left)
        left, right = min(row.left for row in group), max(row.right for row in group)
        top, bottom = min(row.top for row in group), max(row.bottom for row in group)
        merged.append(OcrLine("".join(row.text for row in ordered), min(row.confidence for row in group),
                              ((left, top), (right, top), (right, bottom), (left, bottom))))
    return merged


def roi_pixels(image: object, ratio: tuple[float, float, float, float]) -> tuple[object, tuple[int, ...]]:
    height, width = image.shape[:2]
    left, top, right, bottom = (round(width * ratio[0]), round(height * ratio[1]),
                               round(width * ratio[2]), round(height * ratio[3]))
    if right - left < 16 or bottom - top < 12:
        raise CardAnalysisError("unsupported_video_size", "録画サイズがOCR領域の取得に足りません")
    return image[top:bottom, left:right], (left, top, right, bottom)


def _actor_color(image: object, name: OcrLine, action: OcrLine) -> tuple[str, dict[str, object]]:
    import numpy as np

    height, width = image.shape[:2]
    scale = height / 837.0

    def colors(box: tuple[int, int, int, int], *, header: bool = False) -> tuple[int, int]:
        left, top, right, bottom = box
        pixels = image[max(0, top):min(height, bottom), max(0, left):min(width, right)].astype(float)
        if not pixels.size:
            return 0, 0
        blue, green, red = pixels[..., 0], pixels[..., 1], pixels[..., 2]
        if header:
            return int(np.sum((red > 25) & (red > blue * 1.35) & (red > green * 1.2))), int(np.sum(
                (blue > 35) & (blue > red * 1.35) & (blue > green * 1.15)))
        return int(np.sum((red > 130) & (red > green * 1.45) & (red > blue * 1.30))), int(np.sum(
            (blue > 130) & (blue > red * 1.45) & (blue > green * .92)))

    def actor(red: int, blue: int, minimum: float) -> str:
        if blue > max(red * 1.5, minimum):
            return "self"
        if red > max(blue * 1.5, minimum):
            return "opponent"
        return "unknown"

    red, blue = colors((round(width * .38), round(action.center_y + 18 * scale),
                        round(width * .93), round(action.center_y + 85 * scale)))
    result = actor(red, blue, max(6, 20 * scale * scale))
    header_red, header_blue = colors((0, round(name.center_y - 18 * scale), width,
                                     round(name.center_y + 18 * scale)), header=True)
    method = "arrow_color"
    if result == "unknown":
        result = actor(header_red, header_blue, max(12, 100 * scale * scale))
        method = "header_color" if result != "unknown" else "unknown"
    return result, {"actor_method": method, "arrow_red_pixels": red, "arrow_blue_pixels": blue,
                    "header_red_pixels": header_red, "header_blue_pixels": header_blue,
                    "actor_mapping": "blue=self, red=opponent; recognition candidate only"}


@dataclass(frozen=True)
class LogEntry:
    signature: str
    actor: str
    action: str
    target: Target | None
    confidence: float
    evidence: dict[str, object]


def extract_log_entries(lines: list[OcrLine], image: object, targets: tuple[Target, ...],
                        roi: tuple[int, ...]) -> list[LogEntry]:
    ordered = join_spatial_lines(lines)
    height = image.shape[0]
    scale = height / 837.0
    entries, used_names = [], set()
    for index, action_line in enumerate(ordered):
        action_text = normalize_action_text(action_line.text).strip("・●○◆◇:：。()[]【】")
        action = ACTION_NAMES.get(action_text)
        if action is None:
            continue
        previous = [line for line in ordered[:index] if 8 * scale < action_line.center_y - line.center_y < 90 * scale
                    and len(normalize_card_text(line.text)) >= 2]
        if not previous:
            continue
        name = min(previous, key=lambda row: action_line.center_y - row.center_y)
        matched = match_target(name.text, targets)
        target = matched[0] if matched else None
        signature = f"target:{target.target_id}" if target else normalize_card_text(name.text)
        actor, actor_evidence = _actor_color(image, name, action_line)
        entries.append(LogEntry(signature, actor, action, target, min(name.confidence, action_line.confidence), {
            "kind": "log_use", "supports_use": True, "roi": list(roi), "name_box": name.box,
            "action_box": action_line.box, "name_text": name.text, "action_text": action_line.text,
            "match_ratio": matched[1] if matched else None, "confidence_kind": "ocr_text_score_not_use_probability",
            "row_center_y": name.center_y, **actor_evidence,
        }))
        used_names.add(id(name))
    # 名前が読めても行動が不明なら、使用判定とは別の未分類観測を残す。
    for line in ordered:
        if id(line) in used_names:
            continue
        matched = match_target(line.text, targets)
        if matched is None:
            continue
        target, ratio = matched
        entries.append(LogEntry(f"target:{target.target_id}", "unknown", "unknown", target, line.confidence, {
            "kind": "log_unclassified", "roi": list(roi), "name_box": line.box,
            "name_text": line.text, "action_text": "", "match_ratio": ratio, "row_center_y": line.center_y,
            "confidence_kind": "ocr_text_score_not_use_probability",
        }))
    return sorted(entries, key=lambda row: float(row.evidence["row_center_y"]))


class LogEventTracker:
    """行系列の対応で重複を抑え、同名の新しい行は別の観測にする。"""
    def __init__(self) -> None:
        self.previous: list[LogEntry] = []
        self.previous_ids: list[int] = []
        self.history: dict[tuple[tuple[str, str, str], ...], list[int]] = {}
        self.hidden = False
        self.next_id = 1
        self.observations: dict[int, dict[str, object]] = {}
        self.reappeared_sequences = 0

    @staticmethod
    def _same(first: LogEntry, second: LogEntry) -> bool:
        return first.signature == second.signature and first.action == second.action and (
            first.actor == second.actor or "unknown" in (first.actor, second.actor))

    def observe(self, rows: list[LogEntry], observed_ms: int) -> None:
        if not rows:
            self.hidden = True
            return
        sequence = tuple((row.signature, row.action, row.actor) for row in rows)
        if self.hidden and sequence in self.history:
            self.previous, self.previous_ids = rows, self.history[sequence][:]
            self.hidden = False
            self.reappeared_sequences += 1
            return
        # LCSは同名行の個数と前後関係を保持し、名前だけで一括除去しない。
        first_count, second_count = len(self.previous), len(rows)
        lengths = [[0] * (second_count + 1) for _ in range(first_count + 1)]
        for first in range(first_count - 1, -1, -1):
            for second in range(second_count - 1, -1, -1):
                lengths[first][second] = 1 + lengths[first + 1][second + 1] if self._same(
                    self.previous[first], rows[second]) else max(lengths[first + 1][second], lengths[first][second + 1])
        matched, first, second = {}, 0, 0
        while first < first_count and second < second_count:
            if self._same(self.previous[first], rows[second]):
                matched[second] = self.previous_ids[first]
                first, second = first + 1, second + 1
            elif lengths[first + 1][second] >= lengths[first][second + 1]:
                first += 1
            else:
                second += 1
        ids = []
        for index, row in enumerate(rows):
            event_id = matched.get(index)
            if event_id is None:
                event_id = self.next_id
                self.next_id += 1
                if row.target is not None:
                    source = f"{ANALYZER_VERSION}|log|{event_id}|{row.target.target_id}|{observed_ms}"
                    self.observations[event_id] = {
                        "source_key": hashlib.sha256(source.encode()).hexdigest(), "target_id": row.target.target_id,
                        "card_name": row.target.name, "actor": row.actor, "action": row.action,
                        "observed_ms": observed_ms, "actual_ms": None, "time_kind": "log_observed",
                        "confidence": row.confidence, "evidence": {**row.evidence,
                            "time_meaning": "first_visible_log_observation; may be a past log row",
                            "display_sequence": event_id, "last_observed_ms": observed_ms},
                    }
            elif event_id in self.observations:
                observation = self.observations[event_id]
                observation["evidence"]["last_observed_ms"] = observed_ms
                if observation["actor"] == "unknown" and row.actor != "unknown":
                    observation["actor"] = row.actor
                    observation["evidence"]["actor_later_evidence"] = row.evidence
            ids.append(event_id)
        self.previous, self.previous_ids, self.hidden = rows, ids, False
        self.history[sequence] = ids[:]


class CardFrameAnalyzer:
    def __init__(self, targets: tuple[Target, ...], backend: object) -> None:
        self.targets, self.backend = targets, backend
        self.tracker = LogEventTracker()
        self.details: dict[str, dict[str, object]] = {}
        self.counts = {"sampled_frames": 0, "log_text_frames": 0, "log_use_row_frames": 0,
                       "detail_text_frames": 0, "unmatched_log_rows": 0, "unclassified_target_rows": 0}
        self.last_sample_ms: int | None = None
        self.maximum_sampling_gap_ms = 0
        self.sampling_gap_history_complete = True

    def analyze(self, frame: object, observed_ms: int) -> None:
        detail, detail_roi = roi_pixels(frame, DETAIL_ROI)
        log, log_roi = roi_pixels(frame, LOG_ROI)
        detail_lines, log_lines = self.backend.recognize(detail), self.backend.recognize(log)
        if self.last_sample_ms is not None:
            if observed_ms < self.last_sample_ms:
                raise CardAnalysisError("video_timestamp_invalid", "サンプル時刻が逆行しています")
            self.maximum_sampling_gap_ms = max(self.maximum_sampling_gap_ms, observed_ms - self.last_sample_ms)
        self.last_sample_ms = observed_ms
        self.counts["sampled_frames"] += 1
        self.counts["detail_text_frames"] += bool(detail_lines)
        self.counts["log_text_frames"] += bool(log_lines)
        entries = extract_log_entries(log_lines, log, self.targets, log_roi)
        self.counts["log_use_row_frames"] += any(row.action != "unknown" for row in entries)
        self.counts["unmatched_log_rows"] += sum(row.target is None for row in entries)
        self.counts["unclassified_target_rows"] += sum(row.action == "unknown" for row in entries)
        self.tracker.observe(entries, observed_ms)
        eligible = [line for line in detail_lines if not (line.left > detail.shape[1] * .84
                                                        and line.right - line.left < detail.shape[1] * .12)]
        for line in join_spatial_lines(eligible):
            matched = match_target(line.text, self.targets)
            if matched is None or matched[0].target_id in self.details:
                continue
            target, ratio = matched
            source = f"{ANALYZER_VERSION}|detail|{target.target_id}|{observed_ms}"
            self.details[target.target_id] = {
                "source_key": hashlib.sha256(source.encode()).hexdigest(), "target_id": target.target_id,
                "card_name": target.name, "actor": "unknown", "action": "unknown",
                "observed_ms": observed_ms, "actual_ms": None, "time_kind": "detail_observed",
                "confidence": line.confidence, "evidence": {"kind": "detail_display", "supports_use": False,
                    "roi": list(detail_roi), "name_box": line.box, "name_text": line.text, "match_ratio": ratio,
                    "time_meaning": "card_name_display_only; does not establish use",
                    "confidence_kind": "ocr_text_score_not_use_probability"},
            }

    def observations(self) -> list[dict[str, object]]:
        return sorted([*self.tracker.observations.values(), *self.details.values()],
                      key=lambda row: (row["observed_ms"], row["source_key"]))

    def export_state(self) -> dict[str, object]:
        tracker = self.tracker
        previous = [{"signature": row.signature, "actor": row.actor, "action": row.action,
                     "target_id": row.target.target_id if row.target else None, "confidence": row.confidence,
                     "evidence": row.evidence} for row in tracker.previous]
        return copy.deepcopy({"counts": self.counts, "details": list(self.details.values()),
            "sampling_clock": {"last_sample_ms": self.last_sample_ms,
                "maximum_sampling_gap_ms": self.maximum_sampling_gap_ms,
                "history_complete": self.sampling_gap_history_complete}, "tracker": {
            "previous": previous, "previous_ids": tracker.previous_ids,
            "history": [{"sequence": sequence, "ids": ids} for sequence, ids in tracker.history.items()],
            "hidden": tracker.hidden, "next_id": tracker.next_id,
            "observations": [{"event_id": key, "observation": value} for key, value in tracker.observations.items()],
            "reappeared_sequences": tracker.reappeared_sequences,
        }})

    def restore_state(self, state: object) -> None:
        try:
            if not isinstance(state, dict) or not isinstance(state["counts"], dict) or set(state["counts"]) != set(self.counts):
                raise ValueError("counts")
            if not all(type(value) is int and value >= 0 for value in state["counts"].values()):
                raise ValueError("counts values")
            clock = state.get("sampling_clock")
            if clock is None:
                # 初期v1のcheckpointは実測サンプル間隔を保持していなかった。
                last_sample_ms, maximum_gap, gap_complete = None, 0, False
            else:
                last_sample_ms, maximum_gap, gap_complete = (clock["last_sample_ms"],
                    clock["maximum_sampling_gap_ms"], clock["history_complete"])
                if (last_sample_ms is not None and (type(last_sample_ms) is not int or last_sample_ms < 0)
                        or type(maximum_gap) is not int or maximum_gap < 0 or type(gap_complete) is not bool
                        or gap_complete and state["counts"]["sampled_frames"] > 0 and last_sample_ms is None):
                    raise ValueError("sampling clock")
            targets = {target.target_id: target for target in self.targets}

            def observation(value: object) -> dict[str, object]:
                if not isinstance(value, dict) or value["target_id"] not in targets:
                    raise ValueError("observation target")
                if not isinstance(value["source_key"], str) or re.fullmatch(r"[0-9a-f]{64}", value["source_key"]) is None:
                    raise ValueError("source_key")
                if type(value["observed_ms"]) is not int or value["observed_ms"] < 0 or value["actual_ms"] is not None:
                    raise ValueError("observation time")
                if value["actor"] not in ("self", "opponent", "unknown") or value["action"] not in (*ACTION_NAMES.values(), "unknown"):
                    raise ValueError("observation actor/action")
                if value["time_kind"] not in ("log_observed", "detail_observed") or not isinstance(value["evidence"], dict):
                    raise ValueError("observation evidence")
                confidence = value.get("confidence")
                if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("confidence")
                if value["time_kind"] == "detail_observed" and value["evidence"].get("supports_use") is not False:
                    raise ValueError("detail use")
                return copy.deepcopy(value)

            details = [observation(row) for row in state["details"]]
            saved = state["tracker"]
            if not isinstance(saved, dict) or type(saved["hidden"]) is not bool or type(saved["next_id"]) is not int or saved["next_id"] < 1:
                raise ValueError("tracker")
            previous = []
            for row in saved["previous"]:
                if not isinstance(row["signature"], str) or row["actor"] not in ("self", "opponent", "unknown") or row["action"] not in (*ACTION_NAMES.values(), "unknown"):
                    raise ValueError("previous row")
                target = targets[row["target_id"]] if row["target_id"] is not None else None
                score = row["confidence"]
                if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1 or not isinstance(row["evidence"], dict):
                    raise ValueError("previous evidence")
                previous.append(LogEntry(row["signature"], row["actor"], row["action"], target, score, row["evidence"]))

            def ids(values: object) -> list[int]:
                if not isinstance(values, list) or not all(type(value) is int and 0 < value < saved["next_id"] for value in values):
                    raise ValueError("event ids")
                return values[:]

            previous_ids = ids(saved["previous_ids"])
            if len(previous) != len(previous_ids):
                raise ValueError("previous lengths")
            history = {}
            for record in saved["history"]:
                sequence = tuple(tuple(item) for item in record["sequence"])
                values = ids(record["ids"])
                if len(sequence) != len(values) or not all(len(item) == 3 and all(isinstance(word, str) for word in item) for item in sequence):
                    raise ValueError("history")
                history[sequence] = values
            observations = {}
            for record in saved["observations"]:
                event_id = ids([record["event_id"]])[0]
                if event_id in observations:
                    raise ValueError("duplicate observation")
                observations[event_id] = observation(record["observation"])
            if type(saved["reappeared_sequences"]) is not int or saved["reappeared_sequences"] < 0:
                raise ValueError("reappeared_sequences")
            self.counts = state["counts"].copy()
            self.last_sample_ms, self.maximum_sampling_gap_ms = last_sample_ms, maximum_gap
            self.sampling_gap_history_complete = gap_complete
            self.details = {row["target_id"]: row for row in details}
            self.tracker.previous, self.tracker.previous_ids = previous, previous_ids
            self.tracker.history, self.tracker.hidden = history, saved["hidden"]
            self.tracker.next_id, self.tracker.observations = saved["next_id"], observations
            self.tracker.reappeared_sequences = saved["reappeared_sequences"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CardAnalysisError("invalid_checkpoint", f"解析再開stateが不正です: {exc}") from exc


def source_fingerprint(path: Path, cancel_requested: Callable[[], bool] | None = None) -> dict[str, object]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            if cancel_requested and cancel_requested():
                raise CardAnalysisError("analysis_cancelled", "解析の所有プロセスが終了しました")
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise CardAnalysisError("source_changed", "録画がfingerprint計算中に変更されました")
    return {"path": str(path.resolve()), "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest.hexdigest()}


def _open_video(path: Path) -> object:
    import cv2

    cv2.setNumThreads(1)
    return cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])


def _peak_rss_bytes() -> int | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("page_fault_count", wintypes.DWORD),
            ("peak_working_set_size", ctypes.c_size_t), ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool", ctypes.c_size_t), ("quota_paged_pool", ctypes.c_size_t),
            ("quota_peak_non_paged_pool", ctypes.c_size_t), ("quota_non_paged_pool", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t), ("peak_pagefile_usage", ctypes.c_size_t)]

    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.peak_working_set_size)


def _resume_capture(capture: object, frame_index: int, last_timestamp: float, fps: float,
                    timestamp_fallback: bool, cancel_requested: Callable[[], bool] | None,
                    keepalive: Callable[[], None]) -> str:
    import cv2

    last_update = time.perf_counter()

    def grab() -> bool:
        nonlocal last_update
        if cancel_requested and cancel_requested():
            raise CardAnalysisError("analysis_cancelled", "解析の所有プロセスが終了しました")
        if time.perf_counter() - last_update >= 2:
            keepalive()
            last_update = time.perf_counter()
        return bool(capture.grab())

    # POS_FRAMESのseekはVFRでもframe/fpsの名目時刻に変換される。実際のPTSを照合する。
    if not timestamp_fallback:
        rewind_ms = 1000.0
        while last_timestamp - rewind_ms > 0:
            if capture.set(cv2.CAP_PROP_POS_MSEC, last_timestamp - rewind_ms):
                previous = -1.0
                while grab():
                    timestamp = float(capture.get(cv2.CAP_PROP_POS_MSEC))
                    if not math.isfinite(timestamp) or timestamp < 0 or timestamp <= previous:
                        break
                    if abs(timestamp - last_timestamp) <= .05:
                        return "validated_decoder_pts_seek"
                    if timestamp > last_timestamp:
                        break
                    previous = timestamp
            rewind_ms *= 4

    # 時刻seekが一致しない場合は、OCRを繰り返さず物理frameだけを進めて復元する。
    if not capture.set(cv2.CAP_PROP_POS_FRAMES, 0) or abs(capture.get(cv2.CAP_PROP_POS_FRAMES)) > .5:
        raise CardAnalysisError("video_seek_failed", "動画の解析再開位置へ移動できません")
    previous = -1.0
    for index in range(frame_index):
        if not grab():
            raise CardAnalysisError("video_seek_failed", "解析再開の物理frameへ到達できません")
        timestamp = float(capture.get(cv2.CAP_PROP_POS_MSEC))
        if not math.isfinite(timestamp) or timestamp < 0 or index > 0 and timestamp <= previous:
            timestamp = index * 1000 / fps
        previous = timestamp
    if abs(previous - last_timestamp) > .05 or abs(capture.get(cv2.CAP_PROP_POS_FRAMES) - frame_index) > .5:
        raise CardAnalysisError("video_seek_failed", "解析再開の物理frameと保存PTSが一致しません")
    return "validated_physical_frame_skip"


def analyze_video(request: Mapping[str, object], progress_callback: ProgressCallback | None = None,
                  *, backend: object | None = None, capture_factory: Callable[[Path], object] | None = None,
                  cancel_requested: Callable[[], bool] | None = None) -> dict[str, object]:
    if not isinstance(request, Mapping) or request.get("schema", "card-analysis-request-v1") != "card-analysis-request-v1":
        raise CardAnalysisError("invalid_request", "解析requestのschemaが不正です")
    source = request.get("source_path")
    if not isinstance(source, str) or not source.strip():
        raise CardAnalysisError("invalid_request", "録画パスが不正です")
    path = Path(source).resolve()
    if not path.is_file():
        raise CardAnalysisError("source_missing", "解析する録画がありません")
    targets = parse_targets(request.get("targets"))
    try:
        targets_sha256 = hashlib.sha256(json.dumps(request["targets"], sort_keys=True, ensure_ascii=True,
                                                   allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise CardAnalysisError("invalid_request", "対象snapshotを保存できません") from exc
    sample_fps = request.get("sample_fps", 2.0)
    if isinstance(sample_fps, bool) or not isinstance(sample_fps, (int, float)) or not math.isfinite(sample_fps) or not MIN_SAMPLE_FPS <= sample_fps <= MAX_SAMPLE_FPS:
        raise CardAnalysisError("invalid_request", "解析fpsは0.25以上2.0以下で指定してください")
    expected = request.get("expected_fingerprint")
    if not isinstance(expected, Mapping) or not isinstance(expected.get("size_bytes"), int) or not isinstance(expected.get("mtime_ns"), int):
        raise CardAnalysisError("invalid_request", "録画のサイズと更新時刻のfingerprintが必要です")
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (expected["size_bytes"], expected["mtime_ns"]):
        raise CardAnalysisError("source_changed", "待機中に録画のサイズまたは更新時刻が変わりました")
    expected_hash = expected.get("sha256", "")
    if not isinstance(expected_hash, str) or expected_hash and re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None:
        raise CardAnalysisError("invalid_request", "録画のSHA256の形式が不正です")
    started, cpu_started = time.perf_counter(), time.process_time()

    def progress(value: float, stage: str, observed_ms: int = 0, frames_processed: int = 0) -> None:
        if progress_callback:
            progress_callback({"type": "progress", "progress": min(.99, max(0.0, value)), "stage": stage,
                               "observed_ms": observed_ms, "frames_processed": frames_processed})

    progress(0, "fingerprint")
    before = source_fingerprint(path, cancel_requested)
    if expected_hash and before["sha256"] != expected_hash.lower():
        raise CardAnalysisError("source_changed", "録画のSHA256が待機時と一致しません")
    try:
        import cv2
    except ImportError as exc:
        raise CardAnalysisError("video_dependency_missing", "録画復号のOpenCVを利用できません") from exc
    capture = (capture_factory or _open_video)(path)
    warnings: list[str] = []
    try:
        if not capture.isOpened():
            raise CardAnalysisError("video_open_failed", "録画を復号できません")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count_value = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or not .1 <= fps <= 1000 or not math.isfinite(frame_count_value) or frame_count_value < 1:
            raise CardAnalysisError("video_metadata_invalid", "動画fpsまたは総フレーム数を取得できません")
        frame_count = round(frame_count_value)
        duration_ms = round(frame_count * 1000 / fps)
        if backend is None:
            model_dir = request.get("model_dir")
            if model_dir is not None and not isinstance(model_dir, str):
                raise CardAnalysisError("invalid_request", "モデルディレクトリが不正です")
            backend = create_ocr_backend(Path(model_dir) if model_dir else None)
        analyzer = CardFrameAnalyzer(targets, backend)
        frame_index, next_sample_ms, last_timestamp, timestamp_fallback = 0, 0.0, -1.0, False
        interval_ms = 1000 / float(sample_fps)
        actual_width = actual_height = 0
        resume = request.get("resume_state")
        resume_method = "not_resumed"
        if resume is not None:
            try:
                if not isinstance(resume, dict) or resume["schema"] != "card-analysis-checkpoint-v1" or resume["analyzer_version"] != ANALYZER_VERSION:
                    raise ValueError("schema/analyzer_version")
                if resume["source_fingerprint"] != before or resume["targets_sha256"] != targets_sha256 or resume["sample_fps"] != sample_fps:
                    raise ValueError("source/targets/sample_fps")
                if resume["source_fps"] != fps or resume["reported_frames"] != frame_count:
                    raise ValueError("video metadata")
                frame_index, next_sample_ms, last_timestamp = resume["next_frame_index"], resume["next_sample_ms"], resume["last_timestamp"]
                if type(frame_index) is not int or not 0 <= frame_index <= frame_count:
                    raise ValueError("next_frame_index")
                if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (next_sample_ms, last_timestamp)) or not 0 <= next_sample_ms <= duration_ms + interval_ms or not -1 <= last_timestamp <= duration_ms:
                    raise ValueError("sample clock")
                if type(resume["timestamp_fallback"]) is not bool or not isinstance(resume["warnings"], list) or not all(isinstance(value, str) for value in resume["warnings"]):
                    raise ValueError("warnings")
                timestamp_fallback, warnings = resume["timestamp_fallback"], resume["warnings"][:]
                analyzer.restore_state(resume["state"])
                if analyzer.observations() != resume["observations"] or analyzer.counts["sampled_frames"] > frame_index:
                    raise ValueError("observation/count state")
                actual_width, actual_height = resume["source_width"], resume["source_height"]
                if type(actual_width) is not int or type(actual_height) is not int or actual_width < 0 or actual_height < 0:
                    raise ValueError("source size")
            except (KeyError, TypeError, ValueError) as exc:
                raise CardAnalysisError("invalid_checkpoint", f"解析再開checkpointが不正または不一致です: {exc}") from exc
            if frame_index:
                def resume_progress() -> None:
                    progress(min(.98, last_timestamp / duration_ms), "resume", round(last_timestamp), analyzer.counts["sampled_frames"])

                resume_progress()
                resume_method = _resume_capture(capture, frame_index, last_timestamp, fps,
                    timestamp_fallback, cancel_requested, resume_progress)
        checkpoint_wall, checkpoint_samples = time.perf_counter(), analyzer.counts["sampled_frames"]

        def checkpoint() -> None:
            if progress_callback:
                progress_callback({"type": "checkpoint", "checkpoint": {
                    "schema": "card-analysis-checkpoint-v1", "analyzer_version": ANALYZER_VERSION,
                    "source_fingerprint": before, "targets_sha256": targets_sha256, "sample_fps": sample_fps,
                    "source_fps": fps, "reported_frames": frame_count, "next_frame_index": frame_index,
                    "next_sample_ms": next_sample_ms, "last_timestamp": last_timestamp,
                    "observations": copy.deepcopy(analyzer.observations()), "state": analyzer.export_state(),
                    "timestamp_fallback": timestamp_fallback, "warnings": warnings[:],
                    "source_width": actual_width, "source_height": actual_height,
                }})

        while True:
            if cancel_requested and cancel_requested():
                raise CardAnalysisError("analysis_cancelled", "解析の所有プロセスが終了しました")
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = float(capture.get(cv2.CAP_PROP_POS_MSEC))
            nominal = frame_index * 1000 / fps
            if not math.isfinite(timestamp) or timestamp < 0 or frame_index > 0 and timestamp <= last_timestamp:
                timestamp, timestamp_fallback = nominal, True
            if timestamp < last_timestamp:
                raise CardAnalysisError("video_timestamp_invalid", "動画時刻が逆行しています")
            if abs(timestamp - nominal) > max(1000.0, 3 * 1000 / fps):
                if "variable_frame_timing" not in warnings:
                    warnings.append("variable_frame_timing")
            last_timestamp = timestamp
            if timestamp + .01 >= next_sample_ms:
                actual_height, actual_width = frame.shape[:2]
                analyzer.analyze(frame, round(timestamp))
                next_sample_ms = (math.floor(timestamp / interval_ms) + 1) * interval_ms
                progress(min(.98, timestamp / duration_ms), "analysis", round(timestamp), analyzer.counts["sampled_frames"])
            frame_index += 1
            if analyzer.counts["sampled_frames"] - checkpoint_samples >= 10 or time.perf_counter() - checkpoint_wall >= 2:
                checkpoint()
                checkpoint_wall, checkpoint_samples = time.perf_counter(), analyzer.counts["sampled_frames"]
        decoded_duration_ms = round(last_timestamp + 1000 / fps)
        end_error_ms = abs(decoded_duration_ms - duration_ms)
        end_tolerance_ms = max(2.0, 1.5 * 1000 / fps)
        if frame_index == 0 or end_error_ms > end_tolerance_ms:
            raise CardAnalysisError("video_decode_incomplete",
                f"録画の復号終端が全長と一致しません（{decoded_duration_ms}/{duration_ms}ms, {frame_index}/{frame_count}frames）")
        frame_count_estimated = abs(frame_index - frame_count) > 1
        if frame_count_estimated:
            if timestamp_fallback:
                raise CardAnalysisError("video_frame_count_mismatch", "実PTSを取得できず、総frame数の不一致を検証できません")
            warnings.append("reported_frame_count_is_estimate; EOF verified by terminal decoder timestamp")
        if timestamp_fallback:
            warnings.append("frame_clock_fallback; observed time derived from frame_index/fps")
        if actual_height and abs(actual_width / actual_height - 16 / 9) > .08:
            warnings.append("roi_layout_assumes_16_9_game_screen")
        checkpoint()
        progress(.99, "verify_source", round(last_timestamp), analyzer.counts["sampled_frames"])
        after = source_fingerprint(path, cancel_requested)
        if before != after:
            raise CardAnalysisError("source_changed", "解析中に録画の内容が変更されました")
        peak_rss = _peak_rss_bytes()
        if peak_rss is None:
            warnings.append("worker_peak_rss_unavailable")
        return {
            "schema": "card-analysis-result-v1", "analyzer_version": ANALYZER_VERSION,
            "source_fingerprint": after, "duration_ms": decoded_duration_ms,
            "frames_processed": analyzer.counts["sampled_frames"], "observations": analyzer.observations(),
            "warnings": warnings,
            "coverage": {**analyzer.counts, "decoded_frames": frame_index, "reported_frames": frame_count,
                "source_fps": fps, "sample_fps": sample_fps, "reported_duration_ms": duration_ms,
                "decoded_duration_ms": decoded_duration_ms, "analyzed_start_ms": 0,
                "analyzed_end_ms": decoded_duration_ms, "last_decoded_timestamp_ms": last_timestamp,
                "nominal_sampling_interval_ms": interval_ms,
                "maximum_sampling_gap_ms": analyzer.maximum_sampling_gap_ms if analyzer.sampling_gap_history_complete else None,
                "sampling_gap_history_complete": analyzer.sampling_gap_history_complete,
                "reported_frame_count_estimated": frame_count_estimated,
                "eof_validation": "terminal_decoder_timestamp" if not timestamp_fallback else "full_frame_clock_fallback",
                "terminal_time_error_ms": end_error_ms, "terminal_time_tolerance_ms": end_tolerance_ms,
                "progress_basis": "decoder_timestamp/reported_duration", "resume_method": resume_method,
                "source_width": actual_width, "source_height": actual_height,
                "no_log_evidence_frames": analyzer.counts["sampled_frames"] - analyzer.counts["log_use_row_frames"],
                "past_log_reappearance_sequences": analyzer.tracker.reappeared_sequences,
                "interpretation": "sampled display evidence; hidden logs and missed OCR do not establish non-use",
                "duplicate_limit": "identical logs after hidden intervals cannot prove a new use",
                "time_kind": "decoder_timestamp" if not timestamp_fallback else "frame_clock_fallback"},
            "metrics": {"wall_seconds": time.perf_counter() - started,
                        "cpu_seconds": time.process_time() - cpu_started, "worker_count": 1,
                        "cpu_threads": 1, "opencv_threads": 1, "decoder_threads": 1,
                        "worker_pid": os.getpid(), "peak_rss_bytes": peak_rss,
                        "peak_rss_scope": "current_worker_process_lifetime"},
        }
    finally:
        capture.release()
