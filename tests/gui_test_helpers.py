"""通常GUIの非同期起動と、一時fixtureを閉じる前の終了待機を共有します。"""
from __future__ import annotations

import time


def _wait_for_background(app, window, *, startup_only=False, timeout=10.0):
    deadline = time.monotonic() + timeout
    while window.startup_maintenance_running if startup_only else bool(window.background_tasks):
        app.processEvents()
        window._poll_background_tasks()
        if time.monotonic() >= deadline:
            return False
        # Qtのnative待機だけではPython workerがGILを取得できない場合があります。
        time.sleep(0.005)
    return True


def wait_for_gui_startup(app, window, *, timeout=10.0):
    """smokeへ切り替えず、実際の通常起動が操作可能になるまで待ちます。"""
    completed = _wait_for_background(app, window, startup_only=True, timeout=timeout)
    service = window.service
    if not completed or not service.gui_startup_ready:
        tasks = [(label, future.done(), future.running()) for label, future, _callback in window.background_tasks]
        raise AssertionError(f"通常起動の保存チェックが完了しません: {service.startup_maintenance_message}; tasks={tasks}")
    window.record_state_timer.stop()


def wait_for_gui_tasks(app, window, *, timeout=10.0):
    """検証・削除などの後処理callbackが画面へ反映されるまで待ちます。"""
    if not _wait_for_background(app, window, timeout=timeout):
        tasks = [(label, future.done(), future.running()) for label, future, _callback in window.background_tasks]
        raise AssertionError(f"GUIの背景処理が完了しません: {tasks}")


def close_gui_test_window(app, window, *, timeout=10.0):
    """処理中のDB/ファイル利用が終わってから、一時ディレクトリを解放します。"""
    if not _wait_for_background(app, window, timeout=timeout):
        window.service.close()
    window.background_executor.shutdown(wait=True, cancel_futures=True)
    window._poll_background_tasks()
    for page in window.catalog_pages.values():
        page.baseline = None
    window.history_editor.baseline = None
    window.settings_baseline = None
    window.close()
    window.deleteLater()
    app.processEvents()
