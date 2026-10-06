import csv
import sqlite3
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from PIL import Image
from src.data.hasher import sha256_image, sha256_text
from src.data.models import ItemCreateDTO, ItemDTO, OcrResultDTO
from src.ocr.engine import OcrEngine
from src.ocr.postprocessor import sort_boxes_and_merge


def box(x, text, width=40, confidence=.9):
    return {'box':[[x,0],[x+width,0],[x+width,20],[x,20]], 'text':text, 'confidence':confidence}


@pytest.mark.parametrize('left,right,expected',[
    ('臺灣','稅務','臺灣稅務'), ('OCR','結果','OCR 結果'),
    ('稅務','API','稅務 API'), ('hello','world','hello world'),
    ('12345678','10','12345678 10'),
    ('12','kg','12 kg'), ('12','元','12元'), ('5','%','5%'),
    ('（','已繳稅','（已繳稅'), ('稅款','）','稅款）'),
    ('稅款','，','稅款，'), ('2026/','10/02','2026/10/02'),
    ('NT$','1,234.00','NT$1,234.00'), ('(1,234',')','(1,234)'),
    ('112年','3月','112年3月'), ('a  b','c','a  b c'),
])
def test_spacing_ground_truth(left,right,expected):
    assert sort_boxes_and_merge([box(0,left),box(42,right)])==expected


def test_large_gap_preserves_accounting_columns():
    assert sort_boxes_and_merge([box(0,'借方'),box(100,'100')])=='借方 100'


def test_fusion_same_glyph_conflict_fragments_and_disjoint_repeats():
    def raw(x,t,w=40,c=.9):
        b=box(x,t,w,c); return [b['box'],b['text'],b['confidence']]
    assert OcrEngine._merge_results([raw(0,'己',c=.5)],[raw(0,'已',c=.9)])==[raw(0,'已',c=.9)]
    assert len(OcrEngine._merge_results([raw(0,'同')],[raw(100,'同')]))==2
    fused=OcrEngine._merge_results([raw(0,'臺灣稅務',80,.4)],[raw(0,'臺灣',40,.9),raw(40,'稅務',40,.9)])
    assert len(fused)==2
    assert sort_boxes_and_merge([{'box':a,'text':b,'confidence':c} for a,b,c in fused])=='臺灣稅務'
    assert OcrEngine._merge_results([raw(0,'完整文字',100,.7)],[raw(0,'完整',40,.99)])==[raw(0,'完整文字',100,.7)]


@pytest.mark.parametrize('shape',[(30,2000),(50,1000),(100,3000),(20,500)])
def test_extreme_resize_rejects_before_allocating(monkeypatch,shape):
    from src.ocr.preprocess import upscale_if_small
    def forbidden(*a,**k): raise AssertionError('allocation occurred')
    monkeypatch.setattr('src.ocr.preprocess.resize.cv2.resize',forbidden)
    with pytest.raises(ValueError,match='1600'):
        upscale_if_small(np.zeros((*shape,3),np.uint8))


def test_exact_hash_preserves_whitespace_and_distinct_images(tmp_path):
    assert sha256_text('a')!=sha256_text(' a ')
    a,b=tmp_path/'a.png',tmp_path/'b.png'
    Image.new('RGB',(10,10),(255,0,0)).save(a)
    Image.new('RGB',(10,10),(0,0,255)).save(b)
    assert sha256_image(str(a))!=sha256_image(str(b))


def test_empty_edit_stays_empty(item_repo,make_text_dto):
    i=item_repo.insert(make_text_dto('secret'))
    item_repo.update_edited_text(i,'')
    assert item_repo.get_by_id(i).get_effective_text()==''


@pytest.mark.parametrize('path',['../outside.txt','../../outside.txt','/tmp/outside.txt'])
def test_file_path_escape_rejected(file_mgr,path):
    with pytest.raises(ValueError): file_mgr.get_abs_path(path)


def test_symlink_escape_rejected(file_mgr,tmp_path):
    outside=tmp_path/'outside'; outside.mkdir()
    link=Path(file_mgr._data_dir)/'link'
    try: link.symlink_to(outside,target_is_directory=True)
    except OSError: pytest.skip('OS does not permit symlinks for this account')
    with pytest.raises(ValueError): file_mgr.get_abs_path('link/private.png')


def test_db_connections_are_per_thread_and_writes_survive(tmp_db,item_repo,make_text_dto):
    barrier=threading.Barrier(4)
    def write(n):
        conn=tmp_db.get_connection(); barrier.wait()
        for i in range(50):
            assert item_repo.insert(make_text_dto(f'{n}:{i}'))
        return id(conn)
    with ThreadPoolExecutor(4) as pool:
        identities=list(pool.map(write,range(4)))
    assert len(set(identities))==4
    assert id(tmp_db.get_connection()) not in identities
    assert item_repo.count()==200
    assert tmp_db.get_connection().execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_failed_fts_write_rolls_back_before_next_commit(tmp_db,item_repo,make_text_dto):
    item=item_repo.insert(make_text_dto('old'))
    conn=tmp_db.get_connection()
    conn.execute("CREATE TRIGGER injected AFTER UPDATE ON items BEGIN SELECT RAISE(FAIL,'injected'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError): item_repo.update_edited_text(item,'must rollback')
    conn.execute('DROP TRIGGER injected'); conn.commit()
    item_repo.insert(make_text_dto('next'))
    assert item_repo.get_by_id(item).get_effective_text()=='old'


def test_raw_cleanup_only_after_durable_success(item_repo,file_mgr,make_image_dto,monkeypatch):
    from src.workers.db_worker import DbWorker
    abs_path,rel=file_mgr.save_capture(Image.new('RGB',(20,20)))
    i=item_repo.insert(make_image_dto(rel_path=rel,thumb_path=None))
    worker=DbWorker(item_repo,file_mgr,save_raw_image=False)
    original=item_repo.update_ocr_result
    def failed(*a): raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(item_repo,'update_ocr_result',failed)
    worker.update_ocr(i,OcrResultDTO(text='成功',confidence=.9,status='done'))
    assert Path(abs_path).exists() and item_repo.get_by_id(i).raw_image_path==rel
    monkeypatch.setattr(item_repo,'update_ocr_result',original)
    worker.update_ocr(i,OcrResultDTO(text='',confidence=0,status='failed'))
    assert Path(abs_path).exists() and item_repo.get_by_id(i).ocr_status=='failed'
    worker.update_ocr(i,OcrResultDTO(text='成功',confidence=.9,status='done',engine='actual',model_version='actual-hash'))
    item=item_repo.get_by_id(i)
    assert not Path(abs_path).exists() and item.raw_image_path is None
    assert item.item_type=='text' and item.ocr_engine=='actual' and item.ocr_model_version=='actual-hash'


def test_unlink_failure_preserves_path(item_repo,file_mgr,make_image_dto,monkeypatch):
    from src.workers.db_worker import DbWorker
    abs_path,rel=file_mgr.save_capture(Image.new('RGB',(20,20)))
    i=item_repo.insert(make_image_dto(rel_path=rel))
    def locked(*a,**k): raise PermissionError('locked')
    monkeypatch.setattr(Path,'unlink',locked)
    DbWorker(item_repo,file_mgr,save_raw_image=False).update_ocr(i,OcrResultDTO(text='ok',confidence=.9,status='done'))
    assert item_repo.get_by_id(i).raw_image_path==rel and Path(abs_path).exists()


@pytest.mark.parametrize('payload',['=1+1','+SUM(1,2)','-1+2','@SUM(1,2)','\t=cmd','  =cmd'])
def test_csv_injection_literalized_but_json_preserved(tmp_path,payload):
    import json
    from src.data.exporter import Exporter
    exporter=Exporter(str(tmp_path),str(tmp_path))
    item=ItemDTO(id=1,item_type='text',source_mode='import',text_content=payload)
    with open(exporter.export_csv([item]),encoding='utf-8-sig',newline='') as f:
        rows=list(csv.reader(f))
    assert rows[1][3]=="'"+payload
    assert json.loads(Path(exporter.export_json([item])).read_text())[0]['text']==payload


def test_exports_do_not_overwrite_and_zip_paths_are_unique(tmp_path):
    import zipfile
    from src.data.exporter import Exporter
    for sub in ('a','b'):
        (tmp_path/sub).mkdir(); (tmp_path/sub/'same.png').write_bytes(sub.encode())
    items=[ItemDTO(id=i,item_type='image',source_mode='import',raw_image_path=f'{sub}/same.png') for i,sub in enumerate(('a','b'))]
    exp=Exporter(str(tmp_path),str(tmp_path))
    assert exp.export_txt(items)!=exp.export_txt(items)
    with zipfile.ZipFile(exp.export_zip(items)) as z:
        assert z.read('0/a/same.png')==b'a' and z.read('1/b/same.png')==b'b'
    items[0].raw_image_path='../secret.png'
    before=set(tmp_path.glob('*.zip'))
    with pytest.raises(ValueError): exp.export_zip(items)
    assert set(tmp_path.glob('*.zip'))==before


def test_tagged_settings_dialog_constructs_and_counts(qapp,tag_repo,item_repo,make_text_dto,monkeypatch,tmp_path):
    from src.core.config import ConfigManager
    from src.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr('src.core.config.get_project_root',lambda:str(tmp_path))
    tag=tag_repo.create('稅務');i=item_repo.insert(make_text_dto());tag_repo.add_to_item(i,tag.id)
    dlg=SettingsDialog(ConfigManager(),tag_repo=tag_repo)
    assert dlg._tag_stats_table.item(0,2).text()=='1'
    tag_repo.update(tag.id,'會計','#00ff00')
    assert tag_repo.list_all()[0].name=='會計'
    dlg.deleteLater()


def test_secondary_text_contrast():
    from src.ui.theme import _TEXT_SEC,_BG,_BG_RAISE,_BG_HOVER
    def luminance(hex):
        rgb=[int(hex[i:i+2],16)/255 for i in (1,3,5)]
        return sum(v*w for v,w in zip([c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in rgb],[.2126,.7152,.0722]))
    for background in (_BG,_BG_RAISE,_BG_HOVER):
        assert (luminance(_TEXT_SEC)+.05)/(luminance(background)+.05)>=4.5


def test_model_manifest_fails_closed(tmp_path):
    from src.ocr.model_validator import validate_models
    assert validate_models(str(tmp_path))[0] is False


def test_default_settings_match_packaged_manifest():
    import json
    from src.core.config import DEFAULT_SETTINGS
    root=Path(__file__).resolve().parents[1]
    assert json.loads((root/'config/default_settings.json').read_text())==DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS['clipboard']['monitor_clipboard'] is False


def test_pipeline_returns_original_image_coordinates(monkeypatch):
    engine=OcrEngine(max_image_short_side=384,enable_second_pass=False)
    engine._ready=True
    monkeypatch.setattr(engine,'_do_ocr_array',lambda image:[([[0,0],[100,0],[100,100],[0,100]],'臺灣',.9)])
    result=engine.run_ocr(np.zeros((192,192,3),np.uint8))
    assert result['detail'][0]['box']==[[0.,0.],[50.,0.],[50.,50.],[0.,50.]]
