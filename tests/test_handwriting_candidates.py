"""Pure/Qt candidate contracts. No model, ORT, or human-accuracy claim."""
import io
import struct
import sys
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, Signal, Qt
from src.handwriting.candidates import Candidate, merge_stroke_candidates
from src.handwriting.protocol import (decode_candidate_records, ProtocolError,
                                      encode_strokes, frame, START, MAX_REQUEST_BYTES, HEADER)
from src.handwriting.image_helper import _read, _strokes, _normalize
from src.handwriting.image_controller import decode_image_candidates, _CjkAlphabet


def lane_payload(raw, norm=()):
    output = bytearray(struct.pack('<HBB', 0, len(raw), len(norm)))
    for text in (*raw,*norm):
        label = text.encode()
        output.extend(bytes([len(label)])+label+struct.pack('<f', 1.))
    return output


def test_union_preserves_raw_top30_top10_and_full_sources():
    raw = tuple(chr(0x4e00+i) for i in range(30))
    norm = tuple(chr(0x4e14+i) for i in range(30))
    status, values = decode_candidate_records(lane_payload(raw,norm), set(raw+norm))
    assert status == 0
    assert tuple(v.label for v in values[:10]) == raw[:10]
    assert len(values) == 50 and {v.label for v in values} == set(raw+norm)
    for item in values:
        assert item.raw_rank == (raw.index(item.label)+1 if item.label in raw else 0)
        assert item.normalized_rank == (norm.index(item.label)+1 if item.label in norm else 0)
    assert len(merge_stroke_candidates(raw, tuple(chr(0x5000+i) for i in range(30)))) == 60


@pytest.mark.parametrize('raw,norm', [(('臺','臺'),()), ((),('灣','灣')), (tuple('臺'*31),()), ((),tuple('灣'*31))])
def test_wire_per_lane_duplicate_and_count_rejected(raw,norm):
    with pytest.raises(ProtocolError):
        decode_candidate_records(lane_payload(raw,norm),set('臺灣'))


def test_image_source_and_single_character_budget():
    status, values = decode_image_candidates(lane_payload(('內','吳')), _CjkAlphabet())
    assert status == 0 and all(v.sources == ('影像補辨',) for v in values)
    for raw,norm in [(('內吳',),()), (('A',),()), (('內',),('內',)), (tuple('內吳戶'),())]:
        with pytest.raises(ProtocolError):
            decode_image_candidates(lane_payload(raw,norm),_CjkAlphabet())


def test_image_input_strict_roundtrip_and_normalization():
    strokes=(((0,0),(1000,1000)),((20,30),))
    assert _strokes(encode_strokes(strokes)) == strokes
    for bad in [b'',b'\0\0',encode_strokes(strokes)+b'x',b'\1\0\1\0\xff\xff\0\0']:
        with pytest.raises(ValueError):_strokes(bad)
    assert _normalize((((0,0),(100,0)),)) == (((100,500),(900,500)),)
    assert _normalize((((30,60),),)) == (((500,500),),)
    assert _read(io.BytesIO(frame(START,0))) == (START,0,b'')
    for bad in [b'x',HEADER.pack(b'ZHW1',1,1,0,0,0),HEADER.pack(b'ZHW1',2,1,0,0,MAX_REQUEST_BYTES+1)]:
        with pytest.raises(ValueError):_read(io.BytesIO(bad))


class DisplayController(QObject):
    candidates=Signal(int,object)
    request_failed=Signal(int,str)
    stopped=Signal()
    busy_changed=Signal(bool)
    is_stopped=True
    generation=0
    def resume(self):pass
    def submit(self,strokes):
        self.generation+=1;self.is_stopped=False;self.busy_changed.emit(True);return self.generation
    def stop(self):
        self.is_stopped=True;self.busy_changed.emit(False);self.stopped.emit()


def test_candidate_panel_unique_sources_exact_copy_and_keyboard(qtbot):
    from src.ui.components.candidate_panel import CandidatePanel
    panel=CandidatePanel();qtbot.addWidget(panel);panel.resize(400,240);panel.show();panel.activateWindow();qtbot.wait(10)
    values=[Candidate('內',raw_rank=1),Candidate('內',image_rank=1),Candidate('吳',normalized_rank=2)]
    panel.set_candidates(values,7)
    assert len(panel.buttons)==2 and panel.count.text()=='2 個'
    assert '筆畫原始' in panel.buttons[0].toolTip() and '影像補辨' in panel.buttons[0].toolTip()
    seen=[];panel.selected.connect(lambda *args:seen.append(args))
    panel.buttons[0].setFocus();qtbot.keyClick(panel.buttons[0],Qt.Key.Key_Right)
    assert panel.buttons[1].hasFocus()
    qtbot.keyClick(panel.buttons[1],Qt.Key.Key_Return)
    assert seen==[('吳',7)]


@pytest.mark.parametrize('action',['clear','redraw','close','cancel','new_recognize'])
def test_image_late_result_and_error_cannot_overwrite_retired_revision(qtbot,action):
    from src.ui.handwriting_window import HandwritingWindow
    ink,image=DisplayController(),DisplayController()
    window=HandwritingWindow(controller=ink,image_controller=image);qtbot.addWidget(window);window.show()
    window.pad.buffer.begin(10,10);window.pad.buffer.append(900,900);window.pad.buffer.finish();window._changed()
    window.recognize_image();generation=image.generation;revision=window.pad.buffer.revision
    if action=='clear':window.pad.clear()
    elif action=='redraw':window.pad.buffer.begin(20,30);window._changed()
    elif action=='close':window.close();window.show()
    elif action=='cancel':window.recognize_image()
    else:window.recognize()
    expected=window.status.text()
    image.candidates.emit(generation,(Candidate('內',image_rank=1),))
    image.request_failed.emit(generation,'late failure')
    assert window.status.text()==expected and not window._buttons
    window.close()


def test_child_route_eof_has_no_heavy_imports_or_application_side_effects(tmp_path):
    import subprocess
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-X','importtime',str(root/'src/main.py'),'--handwriting-image-helper'],
                          input=b'',capture_output=True,timeout=5,cwd=tmp_path)
    assert result.returncode==0 and result.stdout==b''
    for name in [b'onnxruntime',b'numpy',b'cv2',b'PySide6',b'src.app',b'src.data']:
        assert name not in result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('values',[[],['二'],['一','一']])
def test_image_frozen_gate_rejects_empty_wrong_or_duplicate_control(values):
    from scripts.verify_candidate_bundle import check_handwriting_image_probe
    from handwriting_release_fixtures import image_probe
    from model_profile_fixtures import fixture_profiles
    models,_=fixture_profiles()
    report=image_probe('1.8.0-rc.1','a'*64,models)
    # Bind to the actual code version, including development overrides.
    from src.core.version import APP_VERSION
    report['version']=APP_VERSION
    report['candidates']=values
    with pytest.raises(ValueError):
        check_handwriting_image_probe(report,{'model_profiles':models,'executable_sha256':'a'*64},True)
