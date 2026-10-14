"""Read PE resources without launching it; bind shell icon and product name."""
from pathlib import Path
import hashlib
import struct


def verify(executable, root):
    import pefile
    from src.core.version import APP_DISPLAY_NAME, APP_NAME
    ico = (Path(root) / 'assets/branding/inkpick.ico').read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', ico)
    if reserved or kind != 1 or count != 9:
        raise ValueError('Invalid canonical InkPick ICO')
    expected = []
    for index in range(count):
        width, height, _, _, _, _, length, offset = struct.unpack_from('<BBBBHHII', ico, 6 + 16 * index)
        expected.append((width, height, ico[offset:offset + length]))
    pe = pefile.PE(str(executable))
    icons, groups = {}, []
    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if entry.id not in (3, 14):
            continue
        for item in entry.directory.entries:
            for language in item.directory.entries:
                data = pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
                if entry.id == 3:
                    icons[item.id] = data
                else:
                    groups.append(data)
    if len(groups) != 1 or struct.unpack_from('<HHH', groups[0]) != (0, 1, count):
        raise ValueError('InkPick PE icon group absent or ambiguous')
    for index, (width, height, data) in enumerate(expected):
        w, h, _, _, _, _, length, icon_id = struct.unpack_from('<BBBBHHIH', groups[0], 6 + 14 * index)
        if (w, h, length) != (width, height, len(data)) or icons.get(icon_id) != data:
            raise ValueError('InkPick PE icon frame mismatch')
    values = {}
    for group in getattr(pe, 'FileInfo', []):
        for info in group:
            if getattr(info, 'Key', None) == b'StringFileInfo':
                for table in info.StringTable:
                    values.update(table.entries)
    if (values.get(b'ProductName', b'').decode('utf-8') != APP_NAME or
            values.get(b'FileDescription', b'').decode('utf-8') != APP_DISPLAY_NAME):
        raise ValueError('InkPick PE product branding mismatch')
    pe.close()
    return {'verified': True, 'icon_frames': count,
            'icon_sha256': hashlib.sha256(ico).hexdigest(), 'product_name': APP_NAME,
            'file_description': APP_DISPLAY_NAME}
