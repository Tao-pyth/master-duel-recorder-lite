import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--card-analysis-worker":
        from master_duel_recorder_lite.card_analysis_worker import main as worker_main
        return worker_main(arguments[1:])
    if arguments and arguments[0] == "--card-analysis-diagnose":
        from master_duel_recorder_lite.card_analysis_worker import worker_standard_streams
        from master_duel_recorder_lite.__main__ import main as cli_main
        with worker_standard_streams():
            return cli_main(["card-analysis", "diagnose", "--json", *arguments[1:]])
    from master_duel_recorder_lite.pyside_gui import main as gui_main
    return gui_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
