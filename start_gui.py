"""GUI entry point for NTE Auto-Fish."""
import argparse
from modules.deps import ensure_dependencies, GUI_PACKAGES

ensure_dependencies(GUI_PACKAGES)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NTE Auto-Fish")
    parser.add_argument(
        "--web", action="store_true",
        help="enable web dashboard (no GUI by default)",
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="also open the GUI window when --web is used",
    )
    parser.add_argument(
        "--web-port", type=int, default=5000, metavar="PORT",
        help="web dashboard port (default: 5000)",
    )
    args = parser.parse_args()

    if args.web and not args.gui:
        # Pure web mode — reuse the headless web runner from main.py
        import argparse as _ap
        from main import _cmd_start
        _cmd_start(_ap.Namespace(web=True, web_port=args.web_port))
    else:
        from gui.app import FishingGUI
        app = FishingGUI(web_port=args.web_port if args.web else None)
        app.run()
