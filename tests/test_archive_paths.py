"""Cross-platform ZIP regression corpus shared by verification and publication.

Synthetic bytes exercise archive boundaries, not a Windows runtime certificate.
"""
import hashlib
import io
import zipfile
from types import SimpleNamespace
import pytest
from scripts.archive_paths import validate_zip_names
from scripts.verify_candidate_bundle import verify_candidate, VERSION
from scripts import publish_prerelease as publisher
from test_candidate_evidence import candidate as candidate_verification
from test_release_publication import candidate as candidate_publication, CONTEXT

BAD_NAMES = ('../outside', '/absolute', 'C:/drive', 'C:relative',
             r'folder\file', r'\\host\share', 'folder/../file',
             'folder/./file', 'folder//file', 'folder/end.', 'folder/end ',
             'folder/file:stream', 'folder/CON.txt', 'folder/lpt1',
             'folder/a\x00hidden', 'folder/a\nb', 'folder/a?b',
             'folder/a|b', 'folder/a<b')


def raw_entry(name):
    info = zipfile.ZipInfo(name)
    # Bypass writer normalization so the reader sees the malicious bytes on
    # Windows too. ZIP metadata is deliberately hostile, not a local OS path.
    info.filename = info.orig_filename = name
    return info


@pytest.mark.parametrize('name', BAD_NAMES)
def test_original_zip_names_rejected_from_actual_archive(name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr(raw_entry(name), b'synthetic')
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        with pytest.raises(ValueError):
            validate_zip_names(archive.infolist())


@pytest.mark.parametrize('original,normalized', [(r'folder\file', 'folder/file'),
                                                ('folder/a\x00b', 'folder/a')])
def test_platform_normalization_cannot_hide_original_name(original, normalized):
    with pytest.raises(ValueError, match='Normalized'):
        validate_zip_names([SimpleNamespace(orig_filename=original, filename=normalized)])


def test_normal_unicode_spaces_directories_and_relative_paths_remain_legal():
    names = ['release/', 'release/手寫 資料.txt', 'release/nested/file.bin']
    validate_zip_names([zipfile.ZipInfo(n) for n in names])


@pytest.mark.parametrize('pair', [('a.txt', 'A.txt'), ('folder/', 'folder'), ('a', 'a')])
def test_case_and_file_directory_aliases_rejected(pair):
    with pytest.raises(ValueError, match='Duplicate'):
        validate_zip_names([zipfile.ZipInfo(n) for n in pair])


def append_bad(bundle, name):
    with zipfile.ZipFile(bundle, 'a') as archive:
        archive.writestr(raw_entry(name), b'synthetic hostile path')
    bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest())


@pytest.mark.parametrize('name', BAD_NAMES)
def test_candidate_gate_rejects_same_path_corpus(candidate_verification, name):
    root, _, _, _ = candidate_verification
    bundle = root / 'artifacts' / f'InkPick-v{VERSION}.zip'
    append_bad(bundle, name)
    with pytest.raises(ValueError, match='(member|path)'):
        verify_candidate(root, 'a'*40)


@pytest.mark.parametrize('name', BAD_NAMES)
def test_publication_rejects_same_path_corpus_before_creating_output(candidate_publication, tmp_path, name):
    root, report, _, _, _ = candidate_publication
    append_bad(root / 'artifacts' / publisher.ZIP_NAME, name)
    output = tmp_path / 'public-output'
    with pytest.raises(publisher.PublicationError, match='verification'):
        publisher.prepare_release(root, report, output, CONTEXT)
    assert not output.exists()

from test_public_release_bundle import bundle as candidate_portable
from scripts.public_release_bundle import BundleError


@pytest.mark.parametrize('name', BAD_NAMES)
def test_public_bundle_rejects_same_path_corpus(candidate_portable, name, tmp_path):
    bundle, _, _, _, run = candidate_portable
    append_bad(bundle, name)
    with pytest.raises(BundleError):
        run()
    assert not (tmp_path/'public.zip').exists()
