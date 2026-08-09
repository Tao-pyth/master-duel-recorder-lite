# 録画対象の選択

## 目的

録画対象を暗黙のデスクトップ全体にせず、利用者がMaster Duel、任意の可視ウィンドウ、モニター、デスクトップ全体から確認して選択できるようにします。

## 対象モデル

`CaptureTarget`は対象種別、安定した表示用識別子、表示名、利用可否を持ちます。ウィンドウは現在のWindowsハンドル、モニターは仮想デスクトップ上の座標とサイズを持ちます。タイトルなし、最小化、極端に小さい補助ウィンドウ、`Program Manager`は任意ウィンドウ候補から除外します。同名ウィンドウはPIDとHWNDを表示して区別します。

Master Duel対象は、設定したプロセス名に属し、任意のタイトル条件を満たす可視・非最小化ウィンドウのうち面積が最大のものです。自動監視は検出時のハンドルを録画開始へそのまま渡すため、別のデスクトップやウィンドウへ暗黙に切り替えません。

## FFmpeg入力

- ウィンドウ: PID・HWNDで選択対象を固定し、FFmpeg `gdigrab`へは対応形式の`-i title=<window title>`を渡す
- モニター: `-offset_x`、`-offset_y`、`-video_size`を指定した`-f gdigrab -i desktop`
- デスクトップ全体: `-f gdigrab -i desktop`

FFmpeg引数は文字列コマンドではなく引数列として構築し、対象識別子をシェルとして解釈しません。保存済みウィンドウハンドルが再起動後に存在しない場合はエラーにし、別対象へ黙って切り替えません。

## 操作

GUIでは録画画面の候補を更新し、対象を選択して保存します。CLIでは`targets`または`targets --json`で候補を確認し、`config set recorder.capture_mode`と`config set recorder.capture_target_id`で選択します。
