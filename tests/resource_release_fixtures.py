"""Synthetic typed receipt for verifier tests, never real performance evidence."""
from copy import deepcopy
from scripts.benchmark_resource_budget import WORKLOAD_CHECKS

def receipt(version, executable_sha256, source_commit, run_id="1234", run_attempt="1"):
    base = {'passed': True, 'samples': 50, 'peak_rss_bytes': 500_000_000,
            'peak_sampled_private_bytes': 550_000_000, 'peak_job_private_bytes': 570_000_000,
            'peak_disk_bytes': 800_000_000, 'max_processes': 3, 'total_processes': 4,
            'maximum_active_mei_directories': 2, 'bootloader_owned_before_resume': True,
            'temporary_cleaned': True, 'exit_code': 0, 'active_processes': 0,
            'job_limit_bytes': 999_997_440, 'inner_report_sha256': 'b'*64, 'inner_report_bytes': 1024}
    manual = deepcopy(base)
    manual.update(workload_checks=dict.fromkeys(WORKLOAD_CHECKS, True), manual_rounds_completed=3,
                  fields_each=5, large_source_pixels=6_000_000, native_capture_used=True)
    return {'schema': 1, 'probe': 'resource_budget', 'version': version, 'platform': 'win32',
            'passed': True, 'limit_bytes': 1_000_000_000, 'sample_interval_ms': 20,
            'source_commit': source_commit, 'executable_sha256': executable_sha256,
            'workflow': {'repository': 'maotai11/desktop-ocr-tool', 'run_id': run_id,
                         'run_attempt': run_attempt, 'source_commit': source_commit},
            'cold_os_cache_verified': False, 'clean_machine_verified': False,
            'stages': {'cold_application': base, 'manual_invoice_and_helpers': manual}}

