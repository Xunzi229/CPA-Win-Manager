"""Open the Mac manager in the browser."""
import sys

from cpa_mac.webapp import main as run


def main():
    if sys.platform != "darwin":
        raise SystemExit("开源软件管理器仅支持 macOS。")
    run()
