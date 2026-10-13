"""汎用日本語OCR。ゲーム素材やカード辞書をモデルへ渡さない。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import importlib.util
import math
from pathlib import Path
import sys


MODEL_HASHES = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}
OCR_VERSIONS = {"rapidocr": "3.10.0", "onnxruntime": "1.31.0"}


class CardAnalysisError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def resolve_ocr_models(model_dir: Path | None = None) -> dict[str, Path]:
    if model_dir is None:
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root is not None:
            model_dir = Path(frozen_root) / "card_ocr_models"
        else:
            spec = importlib.util.find_spec("rapidocr")
            if spec is None or spec.origin is None:
                raise CardAnalysisError("ocr_dependency_missing", "使用カード解析のOCR依存を利用できません")
            model_dir = Path(spec.origin).parent / "models"
    found = {}
    for name, expected in MODEL_HASHES.items():
        path = Path(model_dir) / name
        if not path.is_file():
            raise CardAnalysisError("model_missing", f"OCRモデルがありません: {name}")
        if file_sha256(path) != expected:
            raise CardAnalysisError("model_hash_mismatch", f"OCRモデルのSHA256が一致しません: {name}")
        found[name] = path
    return found


def diagnose_ocr_models(model_dir: Path | None = None) -> dict[str, object]:
    versions = {}
    for package, expected in OCR_VERSIONS.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise CardAnalysisError("ocr_dependency_missing", f"OCR依存がありません: {package}") from exc
        if actual != expected:
            raise CardAnalysisError("ocr_version_mismatch", f"{package}は{expected}が必要です（現在{actual}）")
        versions[package] = actual
    for package in ("opencv-python-headless", "numpy", "Pillow"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    models = resolve_ocr_models(model_dir)
    return {
        "available": True, "backend": "RapidOCR / ONNX Runtime CPU", "versions": versions,
        "models": [{"name": name, "path": str(path), "size_bytes": path.stat().st_size,
                    "sha256": MODEL_HASHES[name]} for name, path in models.items()],
        "settings": {"cpu_threads": 1, "opencv_threads": 1, "det_limit_type": "max",
                     "det_limit_side": 736, "classifier": False, "network_required": False},
    }


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]

    @property
    def left(self) -> float:
        return min(point[0] for point in self.box)

    @property
    def right(self) -> float:
        return max(point[0] for point in self.box)

    @property
    def top(self) -> float:
        return min(point[1] for point in self.box)

    @property
    def bottom(self) -> float:
        return max(point[1] for point in self.box)

    @property
    def center_y(self) -> float:
        return sum(point[1] for point in self.box) / 4

    @property
    def height(self) -> float:
        return (math.dist(self.box[0], self.box[3]) + math.dist(self.box[1], self.box[2])) / 2


class CardOcrBackend:
    def __init__(self, model_dir: Path | None = None) -> None:
        self.diagnostics = diagnose_ocr_models(model_dir)
        models = resolve_ocr_models(model_dir)
        try:
            import cv2
            from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

            cv2.setNumThreads(1)
            self.engine = RapidOCR(params={
                "Global.use_det": True, "Global.use_cls": False, "Global.use_rec": True,
                "Global.text_score": 0.0, "Global.log_level": "warning",
                "Global.use_preprocess_img": True,
                "Det.engine_type": EngineType.ONNXRUNTIME, "Det.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_type": ModelType.SMALL, "Det.lang_type": "japan",
                "Det.model_path": str(models["PP-OCRv6_det_small.onnx"]),
                "Det.limit_type": "max", "Det.limit_side_len": 736,
                "Rec.engine_type": EngineType.ONNXRUNTIME, "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_type": ModelType.SMALL, "Rec.lang_type": "japan",
                "Rec.model_path": str(models["PP-OCRv6_rec_small.onnx"]),
                "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                "EngineConfig.onnxruntime.use_cuda": False,
                "EngineConfig.onnxruntime.use_dml": False,
                "EngineConfig.onnxruntime.use_cann": False,
                "EngineConfig.onnxruntime.use_coreml": False,
            })
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise CardAnalysisError("ocr_initialization_failed", f"OCRを開始できません: {exc}") from exc

    def recognize(self, image: object) -> list[OcrLine]:
        try:
            result = self.engine(image, use_det=True, use_cls=False, use_rec=True, text_score=0.0)
            texts = result.txts
            if texts is None:
                return []
            if result.boxes is None or result.scores is None:
                raise ValueError("OCRの文字・位置・confidenceが揃っていません")
            if not len(texts) == len(result.boxes) == len(result.scores):
                raise ValueError("OCR結果の件数が一致しません")
            lines = []
            for text, box, confidence in zip(texts, result.boxes, result.scores):
                points = tuple((float(x), float(y)) for x, y in box)
                score = float(confidence)
                if len(points) != 4 or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("OCRの位置またはconfidenceが不正です")
                if not all(math.isfinite(value) for point in points for value in point):
                    raise ValueError("OCR位置に不正な数値があります")
                lines.append(OcrLine(str(text), score, points))
            return lines
        except Exception as exc:
            raise CardAnalysisError("ocr_inference_failed", f"OCR推論に失敗しました: {exc}") from exc


def create_ocr_backend(model_dir: Path | None = None) -> CardOcrBackend:
    return CardOcrBackend(model_dir)
