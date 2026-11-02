"""Executed by the frozen bootloader before application imports."""
from src.runtime_policy import configure_runtime
configure_runtime()
import onnxruntime
onnxruntime.disable_telemetry_events()
