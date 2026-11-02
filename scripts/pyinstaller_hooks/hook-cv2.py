"""Use the pinned upstream image bindings without optional video-codec DLLs."""
import runpy
from importlib import metadata
from pathlib import Path

upstream = Path(metadata.distribution('pyinstaller-hooks-contrib').locate_file(
    '_pyinstaller_hooks_contrib/stdhooks/hook-cv2.py'))
if not upstream.is_file() or upstream.resolve() == Path(__file__).resolve():
    raise RuntimeError('Pinned OpenCV packaging hook unavailable')
settings = runpy.run_path(str(upstream))
hiddenimports = settings.get('hiddenimports', [])
excludedimports = settings.get('excludedimports', [])
module_collection_mode = settings.get('module_collection_mode', 'py')
datas = settings.get('datas', [])
binaries = [entry for entry in settings.get('binaries', [])
            if not any(word in Path(entry[0]).name.lower()
                       for word in ('ffmpeg', 'avcodec', 'avformat', 'avutil', 'swscale', 'swresample'))]
