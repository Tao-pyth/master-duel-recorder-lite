# V3.1.0 Windows外部プレイヤーの利用確認

自動削除・画面からの動画削除では、アプリ内の利用leaseに加え、元動画の移動直前と削除直前に `query_file_use(path)` を実行する。Windowsの既定プレイヤーを起動する経路はprocessの寿命を取得できないため、外部の利用もWindowsから照会する。

`video_file_usage.py` は [RmStartSession](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmstartsession) で独立した照会セッションを開き、[RmRegisterResources](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmregisterresources) に対象1ファイルの絶対パスだけを登録し、[RmGetList](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmgetlist) で利用processを取得する。成功したセッションは例外時も [RmEndSession](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmendsession) を呼ぶ。ファイル内容は読み書きせず、他processを終了・再起動しない。

結果 `FileUseStatus(in_use, known, process_ids, message)` の `in_use=True` または `known=False` は削除見送りとする。自己processも一覧から除外しない。DLLやAPIの失敗、利用者一覧の変化が4回で収まらない場合、4096process超、不完全な一覧、確認中のファイル差替えは確認不能とする。Windows以外では `known=True, in_use=False` を返し、既存のleaseとOSのファイル操作失敗を利用する。

実Windowsの隔離試験では、閉じたファイルとハッシュ読込終了後は利用なしを返し、自己processのPython読込、別processのPython読込、`FILE_SHARE_DELETE` を許可する別processの読込ハンドルを利用中として検出した。API失敗・一覧サイズ変化・セッション解放も失敗注入で検証する。すべて一時ディレクトリ内の試験ファイルを対象とし、実行時データを使用しない。

照会だけでは、その直後の新しい読込開始を防げない。破壊直前の照会後には `exclusive_delete_guard` で [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) の削除権限ハンドルを保持する。内容の読込・書込は行わず、共有読込/書込を拒否し、rename/unlinkに必要な共有削除を許す。既存の読込が残る場合や取得不能時は削除を確定しない。原動画のrenameと退避動画のunlinkの各短区間だけ保持し、例外時もCloseHandleする。実Windowsで既存/新規readerの拒否とrename/unlink成功を試験する。アプリ内の利用lease・共通動画操作排他・現物同一性の再確認も併用する。
