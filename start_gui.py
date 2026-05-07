import argparse

from gui.app import FishingGUI

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NTE Auto-Fish GUI")
    parser.add_argument(
        "--web", action="store_true",
        help="enable experimental web dashboard (requires flask)",
    )
    parser.add_argument(
        "--web-port", type=int, default=5000, metavar="PORT",
        help="web dashboard port (default: 5000)",
    )
    args = parser.parse_args()

    app = FishingGUI(web_port=args.web_port if args.web else None)
    app.run()
