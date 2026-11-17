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

def _build_parser():
    parser = argparse.ArgumentParser(description="Desktop OCR Tool")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test-invoice-focus", metavar="REPORT", help="native invoice focus lifecycle probe")
    mode.add_argument("--invoice-focus-source", metavar="REPORT", help=argparse.SUPPRESS)
    mode.add_argument("--self-test-resources", metavar="REPORT", help="real-model bounded invoice resource workload")
    mode.add_argument("--self-test", metavar="REPORT", help="offline OCR and database probe")
    mode.add_argument("--smoke-app", metavar="REPORT", help="isolated application lifecycle probe")
    mode.add_argument("--stress-ocr", metavar="REPORT", help="repeated real-model payload probe")
    mode.add_argument("--self-test-handwriting-image", metavar="REPORT", help="bundled manual raster helper lifecycle probe")
    mode.add_argument("--self-test-handwriting", metavar="REPORT", help="bundled handwriting lifecycle probe")
    mode.add_argument("--self-test-documents", metavar="REPORT", help="offline PDF/image and template payload probe")
    mode.add_argument("--self-test-gui", metavar="REPORT", help="synthetic Qt controls and effects lifecycle probe")
    parser.add_argument('--wait-for-firewall', action='store_true', help='handwriting probe runner handshake only')
    parser.add_argument("--stress-iterations", type=int, default=500)
    return parser


def run(argv=None):
    from src.core.resource_budget import install_runtime_budget
    install_runtime_budget()
    from src.runtime_policy import configure_runtime
    configure_runtime()
    # Child route precedes QApplication, single-instance lock and OCR workers.
    if (sys.argv[1:] if argv is None else argv) == ['--handwriting-image-helper']:
        from src.handwriting.image_helper import run
        return run()
    parser = _build_parser()
    args = parser.parse_args(argv)
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    if args.wait_for_firewall and args.self_test_handwriting is None:
        parser.error('--wait-for-firewall requires --self-test-handwriting')
    if args.self_test_invoice_focus is not None:
        from src.invoice_focus_probe import run_focus_probe
        return run_focus_probe(os.path.abspath(args.self_test_invoice_focus))
    if args.invoice_focus_source is not None:
        from src.invoice_focus_probe import run_source
        return run_source(os.path.abspath(args.invoice_focus_source))
    if args.self_test_resources is not None:
        from src.resource_probe import run_resource_probe
        return run_resource_probe(os.path.abspath(args.self_test_resources))
    if args.self_test_handwriting_image is not None:
        from src.handwriting_image_probe import run_image_probe
        return run_image_probe(os.path.abspath(args.self_test_handwriting_image))
    if args.self_test_handwriting is not None:
        from src.handwriting_probe import run_handwriting_probe
        return run_handwriting_probe(os.path.abspath(args.self_test_handwriting), args.wait_for_firewall)
    if args.self_test_documents is not None:
        from src.document_probe import run_document_probe
        return run_document_probe(os.path.abspath(args.self_test_documents))
    if args.self_test_gui is not None:
        from src.gui_probe import run_gui_probe
        return run_gui_probe(os.path.abspath(args.self_test_gui))
    if args.self_test is not None:
        from src.self_test import run_self_test
        return run_self_test(os.path.abspath(args.self_test))
    if args.stress_ocr is not None:
        from src.runtime_stress import run_runtime_stress
        return run_runtime_stress(os.path.abspath(args.stress_ocr), args.stress_iterations)
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
