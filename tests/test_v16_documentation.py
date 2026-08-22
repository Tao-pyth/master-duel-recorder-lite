from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_v16_post_recording_workflow_document_covers_required_topics() -> None:
    document = (
        PROJECT_ROOT / "docs" / "architecture" / "post-recording-workflow-1.6.0.md"
    ).read_text(encoding="utf-8")

    required_phrases = (
        "戦績管理ページ",
        "`prepare`内部ページの扱い",
        "`improve`内部ページの扱い",
        "設定画面の再編",
        "ダイアログ導線",
        "スクリーンショット回帰と操作スモーク",
        "Master Duel単体音声の診断境界",
        "#506",
        "#514",
        "#515",
        "#520",
        "#530",
        "#531",
        "Master Duel単体音声（DirectShow入力は未使用）",
        "DB schema、設定形式、録画ファイル、queue、manifest、OAuth資格情報を変更しない",
    )
    for phrase in required_phrases:
        assert phrase in document


def test_v16_release_documents_are_linked_from_readme_and_release_notes() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    release_notes = (PROJECT_ROOT / "docs" / "release-notes.md").read_text(
        encoding="utf-8"
    )

    assert "docs/architecture/post-recording-workflow-1.6.0.md" in readme
    assert "V1.6.0: 録画後ワークフロー情報設計" in release_notes
    assert (PROJECT_ROOT / "docs" / "releases" / "1.6.0.md").is_file()
    assert (PROJECT_ROOT / "docs" / "validation" / "1.6.0.md").is_file()
