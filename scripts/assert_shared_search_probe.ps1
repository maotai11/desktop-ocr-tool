# Strict same-candidate checks; this script never starts or changes application work.
function Assert-SharedSearchProbe($Report, [string]$Version, [string]$Hash) {
    if ($Report -isnot [System.Management.Automation.PSCustomObject] -or
        ($Report.schema -isnot [int64] -and $Report.schema -isnot [int]) -or $Report.schema -ne 1 -or
        $Report.probe -cne 'shared_search_lifecycle' -or $Report.fixture_kind -cne 'SYNTHETIC_SHARED_SEARCH' -or
        $Report.version -cne $Version -or $Report.executable_sha256 -cne $Hash -or
        $Report.platform -cne 'win32' -or $Report.qt_platform -cne 'windows') {
        throw 'Shared-search candidate identity mismatch'
    }
    foreach ($key in @('passed', 'frozen', 'overlay_capture_verified')) {
        if ($Report.$key -isnot [bool] -or $Report.$key -ne $true) {
            throw "Incomplete shared-search native capture gate: $key"
        }
    }
    foreach ($key in @('clean_machine_verified', 'ocr_accuracy_tested', 'model_inference_tested')) {
        if ($Report.$key -isnot [bool] -or $Report.$key -ne $false) {
            throw 'Unexpected shared-search accuracy or clean-machine claim'
        }
    }
    $checkNames = @('shared_b_reused', 'multiple_a_rows', 'one_b_match_per_a', 'strict_numbers',
        'ambiguous_requires_selection', 'manual_selection_applied', 'manual_row_shift',
        'blue_calibration', 'blue_follow_stable', 'same_y_scroll_refreshed', 'stale_rows_discarded',
        'capture_overlay_hidden', 'close_stops_polling', 'workers_stopped',
        'clipboard_history_untouched', 'temporary_files_cleaned', 'invoice_match_annotation', 'annotation_pin_persistence',
        'numeric_similarity_review', 'roc_gregorian_date_equivalence',
        'manual_refresh_rechecks_unchanged_invoice', 'annotation_background_opacity',
        'source_region_rows_extracted', 'row_values_exclusive_invoice_positions', 'near_candidates_visible_for_review', 'source_row_arrow_navigation', 'changed_invoice_refreshes_matches')
    if ($Report.checks -isnot [System.Management.Automation.PSCustomObject] -or
        (@($Report.checks.PSObject.Properties.Name | Sort-Object) -join '|') -cne
        (@($checkNames | Sort-Object) -join '|')) {
        throw 'Incomplete shared-search lifecycle checks'
    }
    foreach ($key in $checkNames) {
        if ($Report.checks.$key -isnot [bool] -or $Report.checks.$key -ne $true) {
            throw "Shared-search lifecycle check failed: $key"
        }
    }
    foreach ($key in @('error', 'error_code', 'traceback')) {
        if ($Report.PSObject.Properties.Name -ccontains $key) { throw 'Shared-search failure diagnostic present' }
    }
}
