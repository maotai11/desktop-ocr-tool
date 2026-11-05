# Strict same-candidate checks; this script never starts or changes application work.
function Assert-GuiProbe($Report, [string]$Version, [string]$Hash) {
    if ($Report -isnot [System.Management.Automation.PSCustomObject] -or
        ($Report.schema -isnot [int64] -and $Report.schema -isnot [int]) -or $Report.schema -ne 1 -or
        $Report.probe -cne 'gui_feature_lifecycle' -or $Report.fixture_kind -cne 'SYNTHETIC_GUI_CONTROLS' -or
        $Report.version -cne $Version -or $Report.executable_sha256 -cne $Hash -or
        $Report.platform -cne 'win32' -or $Report.qt_platform -cne 'windows') {
        throw 'GUI feature candidate identity mismatch'
    }
    foreach ($key in @('passed', 'frozen')) {
        if ($Report.$key -isnot [bool] -or $Report.$key -ne $true) {
            throw "Incomplete GUI feature native capture gate: $key"
        }
    }
    foreach ($key in @('clean_machine_verified', 'ocr_accuracy_tested', 'model_inference_tested')) {
        if ($Report.$key -isnot [bool] -or $Report.$key -ne $false) {
            throw 'Unexpected GUI feature accuracy or clean-machine claim'
        }
    }
    $checkNames = @('launcher_actions_and_capture_cancel', 'settings_cancel_and_apply', 'history_search_tags_and_pin',
        'editor_save_and_history_refresh', 'ocr_selection_copy_explicit', 'document_import_template_zoom_clear',
        'handwriting_stroke_candidate_copy', 'paired_comparison_controls', 'shared_search_controls',
        'tray_pause_and_quit_route', 'windows_and_workers_closed', 'temporary_files_cleaned')
    if ($Report.checks -isnot [System.Management.Automation.PSCustomObject] -or
        (@($Report.checks.PSObject.Properties.Name | Sort-Object) -join '|') -cne
        (@($checkNames | Sort-Object) -join '|')) {
        throw 'Incomplete GUI feature lifecycle checks'
    }
    foreach ($key in $checkNames) {
        if ($Report.checks.$key -isnot [bool] -or $Report.checks.$key -ne $true) {
            throw "GUI feature lifecycle check failed: $key"
        }
    }
    foreach ($key in @('error', 'error_code', 'traceback')) {
        if ($Report.PSObject.Properties.Name -ccontains $key) { throw 'GUI feature failure diagnostic present' }
    }
}
