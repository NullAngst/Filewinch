"""FileWinch: GUI only. `python -m filewinch` or the AppImage opens the window."""

import sys


def main() -> int:
    if sys.platform != "linux":
        print("FileWinch runs on Linux only.", file=sys.stderr)
        return 1
    from filewinch.ui.main_window import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
