# Third-party notices

## RapidOCR / PaddleOCR generic OCR models

The optional card analysis uses RapidOCR 3.10.0 and two generic PP-OCRv6 small
ONNX models derived from official PaddleOCR models. The CLI and GUI distributions
include these models and their license and provenance documents under
`licenses/card-ocr/`. The updater does not include the OCR runtime or models.

RapidOCR source code and engineering components are copyright RapidOCR Authors
and licensed under Apache License 2.0. The upstream model weights are copyright
Baidu and/or the applicable PaddleOCR rights holders, under Apache License 2.0.
RapidOCR converted and packaged the upstream models for ONNX Runtime. The upstream
ownership and license terms are retained. No affiliation or endorsement is implied.

- `PP-OCRv6_det_small.onnx`: SHA-256
  `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f`
- `PP-OCRv6_rec_small.onnx`: SHA-256
  `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884`

See `licenses/card-ocr/APACHE-2.0.txt`, `PaddleOCR-LICENSE.txt`,
`RapidOCR-LICENSE.txt`, `RapidOCR-MODEL_LICENSES.md` and `MODEL_SOURCES.json`.
The full upstream texts and exact source URLs are preserved there. Only generic
OCR weights and configuration are bundled; game images, extracted templates,
recordings and a full official card database are not distribution assets.

## ONNX Runtime and OCR Python dependencies

ONNX Runtime 1.31.0 is copyright Microsoft Corporation, distributed under the
MIT License. The complete text is in `licenses/card-ocr/ONNXRuntime-LICENSE.txt`.
Release builds retain the installed distribution metadata, license files and
copyright notices of RapidOCR, ONNX Runtime and their Python dependencies,
including OpenCV, NumPy, Shapely, PyClipper and Pillow. These components retain
their individual license terms; the project's MIT license does not replace them.

## PySide6 / Qt for Python

The optional in-app review preview uses PySide6 / Qt for Python when the
`review` extra or release build dependencies are installed. PySide6 and Qt are
provided by The Qt Company and contributors under the licenses published with
the PySide6 wheels and Qt runtime components, including LGPL terms for the Qt
libraries used by this project.

See:

- https://doc.qt.io/qtforpython/licenses.html
- https://www.qt.io/licensing/open-source-lgpl-obligations

## Microsoft Windows classic samples

The `mdrl-audio-loopback` helper contains code derived from the Microsoft
Application Loopback sample in `microsoft/Windows-classic-samples`.

Copyright (c) Microsoft Corporation.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
