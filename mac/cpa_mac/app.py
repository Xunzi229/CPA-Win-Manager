"""Open the Mac manager in the browser."""
import sys

from cpa_mac.webapp import main as run


def main():
    if sys.platform != "darwin":
        raise SystemExit("CPA Mac 管理器仅支持 macOS。")
    run()
