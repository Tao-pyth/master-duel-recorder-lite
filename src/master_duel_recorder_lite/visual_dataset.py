from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import struct
import subprocess
from typing import Any, BinaryIO

from .frame_capture import FrameSample
from .visual_detection import DetectionCandidate, TemporalEventConsensus, VisualDetectionPipeline
from .windows_process import configure_windows_process_errors, subprocess_creation_flags


@dataclass(frozen=True)
class EventAnnotation:
    event_type: str
    window_start_ms: int
    window_end_ms: int
    actor: str | None = None
    outcome: str | None = None
    play_order: str | None = None


@dataclass(frozen=True)
class VideoDatasetEntry:
    video_id: str
    file: Path
    source: str
    display_profile: str
    duel_type: str
    has_audio: bool | None
    events: tuple[EventAnnotation, ...]
    strict_event_types: tuple[str, ...]


@dataclass(frozen=True)
class VisualDataset:
    dataset_id: str
    manifest_path: Path
    videos: tuple[VideoDatasetEntry, ...]


@dataclass(frozen=True)
class EventEvaluation:
    video_id: str
    event_type: str
    expected_ms: int | None
    detected_ms: int | None
    matched: bool
    error_ms: int | None
    detail: str


@dataclass(frozen=True)
class VideoEvaluation:
    video_id: str
    status: str
    detected_count: int
    evaluations: tuple[EventEvaluation, ...]
    error: str | None = None


@dataclass(frozen=True)
class DatasetEvaluation:
    dataset_id: str
    sample_fps: float
    videos: tuple[VideoEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def metric(self, event_type: str) -> tuple[int, int, int]:
        items = [
            item
            for video in self.videos
            for item in video.evaluations
            if item.event_type == event_type
        ]
        true_positive = sum(item.matched for item in items if item.expected_ms is not None)
        false_negative = sum(not item.matched for item in items if item.expected_ms is not None)
        false_positive = sum(not item.matched for item in items if item.expected_ms is None)
        return true_positive, false_positive, false_negative


def load_visual_dataset(manifest_path: Path) -> VisualDataset:
    manifest = manifest_path.resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("画面判定manifestのschema_versionは1である必要があります")
    videos: list[VideoDatasetEntry] = []
    ids: set[str] = set()
    for raw in payload.get("videos", []):
        video_id = _required_text(raw, "id")
        if video_id in ids:
            raise ValueError(f"動画IDが重複しています: {video_id}")
        ids.add(video_id)
        source = raw.get("source", "live")
        if source not in {"live", "replay"}:
            raise ValueError(f"未対応の解析sourceです: {source}")
        annotations = tuple(
            EventAnnotation(
                event_type=_required_text(item, "event_type"),
                window_start_ms=int(item["window_start_ms"]),
                window_end_ms=int(item["window_end_ms"]),
                actor=item.get("actor"),
                outcome=item.get("outcome"),
                play_order=item.get("play_order"),
            )
            for item in raw.get("events", [])
        )
        for item in annotations:
            if item.window_start_ms < 0 or item.window_end_ms < item.window_start_ms:
                raise ValueError(f"正解区間が不正です: {video_id}/{item.event_type}")
        video_path = Path(_required_text(raw, "file"))
        if not video_path.is_absolute():
            video_path = manifest.parent / video_path
        videos.append(
            VideoDatasetEntry(
                video_id=video_id,
                file=video_path.resolve(),
                source=source,
                display_profile=_required_text(raw, "display_profile"),
                duel_type=str(raw.get("duel_type", "unknown")),
                has_audio=raw.get("has_audio"),
                events=annotations,
                strict_event_types=tuple(str(item) for item in raw.get("strict_event_types", [])),
            )
        )
    return VisualDataset(_required_text(payload, "dataset_id"), manifest, tuple(videos))


def analyze_video(
    ffmpeg: Path,
    entry: VideoDatasetEntry,
    *,
    sample_fps: float = 2.0,
) -> tuple[DetectionCandidate, ...]:
    if not entry.file.is_file():
        raise FileNotFoundError(entry.file)
    if sample_fps <= 0 or sample_fps > 2:
        raise ValueError("sample_fpsは0より大きく2以下である必要があります")
    configure_windows_process_errors()
    process = subprocess.Popen(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(entry.file),
            "-vf",
            f"fps={sample_fps:g},scale=640:-2",
            "-an",
            "-f",
            "image2pipe",
            "-vcodec",
            "bmp",
            "-",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess_creation_flags(),
    )
    assert process.stdout is not None
    maximum_gap_ms = max(2500, round(2200 / sample_fps))

    def new_pipeline() -> VisualDetectionPipeline:
        return VisualDetectionPipeline(
            consensus=TemporalEventConsensus(
                source=entry.source,
                maximum_gap_ms=maximum_gap_ms,
            )
        )

    pipeline = new_pipeline()
    completed_events: list[DetectionCandidate] = []
    session_events: list[DetectionCandidate] = []
    restart_after_error = entry.source == "live" and any(
        item.event_type == "duel_start" for item in entry.events
    )
    frame_index = 0
    try:
        while header := _read_exact(process.stdout, 6):
            if header[:2] != b"BM":
                raise RuntimeError("FFmpegから不正なBMPフレームを受信しました")
            size = struct.unpack_from("<I", header, 2)[0]
            data = header + _read_exact(process.stdout, size - 6, required=True)
            width = struct.unpack_from("<i", data, 18)[0]
            height = abs(struct.unpack_from("<i", data, 22)[0])
            elapsed_ms = round(frame_index * 1000 / sample_fps)
            sample = FrameSample(
                captured_at=datetime.now(timezone.utc),
                window_handle=0,
                window_title=entry.video_id,
                width=width,
                height=height,
                pixel_format="bmp",
                data=data,
            )
            detected = pipeline.analyze(sample, elapsed_ms)
            error = next((item for item in detected if item.event_type == "match_error"), None)
            if error is not None:
                completed_events.append(error)
                session_events.clear()
                if restart_after_error:
                    pipeline = new_pipeline()
            else:
                session_events.extend(detected)
            frame_index += 1
    except BaseException:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        raise
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    returncode = process.wait(timeout=10)
    if returncode != 0:
        raise RuntimeError(stderr.strip() or f"FFmpegが終了コード{returncode}で失敗しました")
    return tuple((*completed_events, *session_events))


def evaluate_visual_dataset(
    dataset: VisualDataset,
    ffmpeg: Path,
    *,
    sample_fps: float = 2.0,
    max_workers: int = 1,
) -> DatasetEvaluation:
    if max_workers < 1:
        raise ValueError("max_workersは1以上である必要があります")
    if max_workers == 1:
        results = [_evaluate_video_entry(entry, ffmpeg, sample_fps) for entry in dataset.videos]
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            results = list(
                executor.map(
                    _evaluate_video_entry,
                    dataset.videos,
                    (ffmpeg for _entry in dataset.videos),
                    (sample_fps for _entry in dataset.videos),
                )
            )
    return DatasetEvaluation(dataset.dataset_id, sample_fps, tuple(results))


def _evaluate_video_entry(
    entry: VideoDatasetEntry,
    ffmpeg: Path,
    sample_fps: float,
) -> VideoEvaluation:
    if not entry.file.is_file():
        return VideoEvaluation(entry.video_id, "skipped", 0, (), "動画未配置")
    try:
        detected = analyze_video(ffmpeg, entry, sample_fps=sample_fps)
        evaluations = _match_events(entry, detected)
        return VideoEvaluation(entry.video_id, "evaluated", len(detected), evaluations)
    except Exception as exc:
        return VideoEvaluation(entry.video_id, "error", 0, (), str(exc))


def render_evaluation_markdown(report: DatasetEvaluation) -> str:
    event_types = sorted(
        {
            item.event_type
            for video in report.videos
            for item in video.evaluations
        }
    )
    lines = [
        f"# {report.dataset_id} 画面判定評価",
        "",
        f"サンプリング: {report.sample_fps:g} fps",
        "",
        "| イベント | TP | FP | FN | Precision | Recall |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for event_type in event_types:
        tp, fp, fn = report.metric(event_type)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        lines.append(
            f"| {event_type} | {tp} | {fp} | {fn} | {precision:.3f} | {recall:.3f} |"
        )
    lines.extend(["", "| 動画ID | 状態 | 検出数 | 詳細 |", "|---|---|---:|---|"])
    for video in report.videos:
        details = video.error or ", ".join(
            f"{item.event_type}:{'OK' if item.matched else 'NG'}"
            + (f"({item.error_ms:+d}ms)" if item.error_ms is not None else "")
            for item in video.evaluations
        )
        lines.append(f"| {video.video_id} | {video.status} | {video.detected_count} | {details} |")
    return "\n".join(lines) + "\n"


def _match_events(
    entry: VideoDatasetEntry, detected: tuple[DetectionCandidate, ...]
) -> tuple[EventEvaluation, ...]:
    remaining = list(detected)
    results: list[EventEvaluation] = []
    for expected in entry.events:
        match = next(
            (
                item
                for item in remaining
                if item.event_type == expected.event_type
                and expected.window_start_ms <= item.elapsed_ms <= expected.window_end_ms
                and _attributes_match(expected, item)
            ),
            None,
        )
        midpoint = (expected.window_start_ms + expected.window_end_ms) // 2
        if match is not None:
            remaining.remove(match)
            results.append(
                EventEvaluation(
                    entry.video_id,
                    expected.event_type,
                    midpoint,
                    match.elapsed_ms,
                    True,
                    match.elapsed_ms - midpoint,
                    match.reason,
                )
            )
        else:
            results.append(
                EventEvaluation(
                    entry.video_id,
                    expected.event_type,
                    midpoint,
                    None,
                    False,
                    None,
                    "正解区間内に候補がありません",
                )
            )
    for item in remaining:
        if item.event_type not in entry.strict_event_types:
            continue
        results.append(
            EventEvaluation(
                entry.video_id,
                item.event_type,
                None,
                item.elapsed_ms,
                False,
                None,
                item.reason,
            )
        )
    return tuple(results)


def _attributes_match(expected: EventAnnotation, actual: DetectionCandidate) -> bool:
    return all(
        wanted is None or wanted == observed
        for wanted, observed in (
            (expected.actor, actual.actor),
            (expected.outcome, actual.outcome),
            (expected.play_order, actual.play_order),
        )
    )


def _read_exact(stream: BinaryIO, size: int, *, required: bool = False) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            if required or chunks:
                raise EOFError("BMPフレームが途中で終了しました")
            return b""
        chunks.extend(part)
    return bytes(chunks)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"manifestの{key}は必須です")
    return value
