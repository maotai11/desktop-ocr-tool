"""Use blocking ORT worker waits instead of CPU spinning between requests."""
from contextlib import contextmanager
from threading import RLock

_SESSION_CREATION = RLock()


@contextmanager
def non_spinning_sessions():
    """Configure the pinned wrapper's session factory only while it loads.

    RapidOCR 1.4.4 does not expose ORT session-config entries. Its three
    constructors use this same static factory. Serialize our loads and restore
    the original descriptor even on failure; loaded sessions retain the policy.
    No vendor files or process-wide runtime options are modified permanently.
    """
    from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession

    with _SESSION_CREATION:
        original = OrtInferSession.__dict__['_init_sess_opts']

        def options(config):
            value = original.__func__(config)
            value.add_session_config_entry('session.intra_op.allow_spinning', '0')
            value.add_session_config_entry('session.inter_op.allow_spinning', '0')
            return value

        OrtInferSession._init_sess_opts = staticmethod(options)
        try:
            yield
        finally:
            OrtInferSession._init_sess_opts = original
