"""Executed by the frozen bootloader before application imports."""
import os
os.environ['ORT_DISABLE_TELEMETRY'] = '1'
import onnxruntime
onnxruntime.disable_telemetry_events()
