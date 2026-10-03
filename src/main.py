import argparse
import logging
import os
import sys
import tempfile

# 開發模式：確保 project root 在 sys.path（frozen EXE 由 PyInstaller 處理，不需要）
if not getattr(sys, 'frozen', False):
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

def run(argv=None):
    parser = argparse.ArgumentParser(description="Desktop OCR Tool")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", metavar="REPORT", help="offline OCR and database probe")
    mode.add_argument("--smoke-app", metavar="REPORT", help="isolated application lifecycle probe")
    args = parser.parse_args(argv)
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    if args.self_test is not None:
        from src.self_test import run_self_test
        return run_self_test(os.path.abspath(args.self_test))
    from src.app import main
    if args.smoke_app is not None:
        previous_home = os.environ.get("DESKTOP_OCR_HOME")
        try:
            with tempfile.TemporaryDirectory(prefix="desktop-ocr-smoke-") as directory:
                os.environ["DESKTOP_OCR_HOME"] = directory
                try:
                    return main(smoke_report=os.path.abspath(args.smoke_app))
                finally:
                    logging.shutdown()
        finally:
            if previous_home is None:
                os.environ.pop("DESKTOP_OCR_HOME", None)
            else:
                os.environ["DESKTOP_OCR_HOME"] = previous_home
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
