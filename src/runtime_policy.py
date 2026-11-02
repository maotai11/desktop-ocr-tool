"""Disable optional backends the offline image-only application never uses."""
import os


def configure_runtime():
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '0'
    os.environ['OPENCV_IO_ENABLE_JASPER'] = '0'
    os.environ['OPENCV_VIDEOIO_PRIORITY_FFMPEG'] = '0'
    os.environ['OPENCV_VIDEOIO_PRIORITY_GSTREAMER'] = '0'
