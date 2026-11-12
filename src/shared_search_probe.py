"""Synthetic shared-table controller/worker proof; no OCR accuracy claim."""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from threading import Event

CHECK_NAMES = (
    'shared_b_reused', 'multiple_a_rows', 'one_b_match_per_a', 'strict_numbers',
    'ambiguous_requires_selection', 'manual_selection_applied', 'manual_row_shift',
    'blue_calibration', 'blue_follow_stable', 'same_y_scroll_refreshed',
    'stale_rows_discarded', 'capture_overlay_hidden', 'close_stops_polling',
    'workers_stopped', 'clipboard_history_untouched', 'temporary_files_cleaned',
    'invoice_match_annotation', 'annotation_pin_persistence',
    'numeric_similarity_review', 'roc_gregorian_date_equivalence',
    'manual_refresh_rechecks_unchanged_invoice', 'annotation_background_opacity',
    'source_region_rows_extracted', 'row_values_exclusive_invoice_positions',
    'near_candidates_visible_for_review', 'source_row_arrow_navigation',
    'changed_invoice_refreshes_matches',
)


def _wait(milliseconds):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _until(predicate, timeout=5000):
    from PySide6.QtCore import QEventLoop, QTimer
    if predicate():
        return
    loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
    poll.setInterval(5)
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout)
    loop.exec()
    poll.stop()
    deadline.stop()
    if not predicate():
        raise ValueError('Synthetic shared-search deadline')


def _close_resources(window, controller, worker):
    if window is not None:
        window.shutdown()
    elif controller is not None:
        controller.shutdown()
    if worker is not None:
        worker.stop()
    capture_stopped = controller is None or controller._capture.wait(5000)
    ocr_stopped = worker is None or worker.wait(5000)
    if window is not None:
        window.highlight_overlay.deleteLater()
        window.resume_button.deleteLater()
        window.manual_button.deleteLater()
        window.deleteLater()
    return bool(capture_stopped and ocr_stopped)


def _row(text, y):
    return {'text': text, 'confidence': 1., 'box': [[4, y], [140, y], [140, y + 12], [4, y + 12]]}


def _logical_checks(app):
    """Drive real MSS cropping, controllers and the shared worker queue."""
    import numpy as np
    from PySide6.QtCore import Qt
    from .comparison import shared_capture as capture_module
    from .comparison.shared_controller import SharedSearchController
    from .ui.shared_search_window import SharedSearchWindow
    from .workers.ocr_worker import OcrWorker

    checks = dict.fromkeys(CHECK_NAMES, False)
    screen_factory = capture_module.mss.mss
    window = controller = worker = None
    history, snapshots, states, calls, captures = [], [], [], [], []
    entered, release, hidden = Event(), Event(), Event()
    hide_observations = []
    pixels = {'a40': (11, 12), 'a60': (14, 15), 'b': 200}
    clipboard = app.clipboard().text()

    class Screen:
        monitors = ({'left': 0, 'top': 0, 'width': 1200, 'height': 1000},) * 2
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def grab(self, region):
            if not hidden.is_set():
                raise ValueError('Capture preceded overlay hide')
            captures.append(dict(region))
            canvas = np.full((1000, 1200, 4), 25, np.uint8)
            canvas[:, :, 3] = 255
            for y in (40, 60):
                for x, value in zip((20, 180), pixels[f'a{y}']):
                    canvas[y:y + 12, x:x + 50, :3] = value
            canvas[20:720, 230:780, :3] = pixels['b']
            x, y, w, h = (region[name] for name in ('left', 'top', 'width', 'height'))
            return canvas[y:y + h, x:x + w].copy()

    class Oracle:
        def set_progress_callback(self, callback):
            pass
        def is_ready(self):
            return True
        def run_ocr(self, image, mode):
            value = int(image[0, 0, 0])
            calls.append(value)
            if value == 17:
                entered.set()
                if not release.wait(5):
                    raise ValueError('Stale-row handshake')
            if value >= 200:
                lines = (['12346', '西元2026年10月12日'] if value == 206 else
                         ['訂單AB123', '數量123', '新列文字', '新捲動資料', '過期列', '目前列'])
                if value == 201:
                    lines.append('訂單AB123')
                return {'text': '\n'.join(lines), 'status': 'done',
                        'detail': [_row(text, 5 + i * 48) for i, text in enumerate(lines)]}
            text = {11: '訂單AB123', 12: '1.23', 13: '訂單AB123', 14: '新列文字',
                    15: '123', 16: '新捲動資料', 17: '過期列', 18: '目前列', 19: '訂單AB123',
                    20: '12345', 21: '民國115年10月12日'}[value]
            return {'text': text, 'status': 'done', 'detail': []}

    def ready(action):
        count = len(snapshots)
        if action() is False:
            raise ValueError('Synthetic action refused')
        _until(lambda: len(snapshots) > count or not controller.running)
        if len(snapshots) <= count:
            raise ValueError('Synthetic capture stopped before verification')
        if snapshots[-1]['state'] != 'ready':
            raise ValueError('Synthetic result was not verified')
        return snapshots[-1]

    try:
        capture_module.mss.mss = Screen
        capture = capture_module.SharedCaptureWorker(capture=capture_module.capture_shared)
        worker = OcrWorker(Oracle())
        controller = SharedSearchController(worker, capture_worker=capture)
        window = SharedSearchWindow(controller)
        window._source_mode.setCurrentIndex(window._source_mode.findData('fields'))
        def before():
            visible = window.highlight_overlay.isVisible()
            accepted = window.before_capture()
            is_hidden = not window.highlight_overlay.isVisible()
            hide_observations.append((visible, accepted, is_hidden))
            if accepted and is_hidden:
                hidden.set()
            return accepted
        def after():
            window.after_capture()
            hidden.clear()
        controller.before_capture, controller.after_capture = before, after
        controller.updated.connect(lambda value: snapshots.append(value))
        controller.state_changed.connect(states.append)
        worker.ocr_done.connect(lambda *value: history.append(value))
        first = controller.fields[0]['id']
        second = controller.add_field('數字欄')
        assert second is not None
        assert controller.set_a_region(first, (20, 40, 50, 12, 1))
        assert controller.set_a_region(second, (180, 40, 50, 12, 1))
        assert controller.set_b_region((230, 20, 550, 700, 1))
        assert controller.set_row_pitch(20)
        controller.set_interval(1)
        initial = ready(controller.start)
        fields = initial['fields']
        checks['multiple_a_rows'] = len(fields) == len(window._fields_cards) == 2
        checks['strict_numbers'] = fields[0]['result']['status'] == 'matched' and fields[1]['result']['status'] == 'missing'
        checks['one_b_match_per_a'] = all(
            sum(candidate['id'] == field['result']['selected_id']
                for candidate in field['result']['candidates']) <= 1 for field in fields)

        controller.pause()
        pixels['a40'] = (13, 19)
        reused = ready(controller.start)['fields']
        checks['shared_b_reused'] = (calls.count(200) == 1 and calls.count(13) == 1
            and sum(field['result']['status'] == 'matched' for field in reused) == 1
            and sum(field['result']['selected_id'] is not None for field in reused) == 1
            and any(field['result']['status'] == 'uncertain'
                    and field['result']['selected_id'] is None for field in reused))
        # Use separate detected invoice lines for the placement proof. Two A
        # values must reserve distinct B positions, including legacy fields.
        controller.pause()
        pixels['a40'] = (13, 15)
        distinct = ready(controller.start)['fields']
        callouts = window.highlight_overlay.callouts
        fields_by_name = {field['name']: field for field in distinct}
        dpr = app.primaryScreen().devicePixelRatio()
        def selected_bounds(field):
            result = field['result']
            chosen = next(candidate for candidate in result['candidates']
                          if candidate['id'] == result['selected_id'])
            points = [point for box in chosen['boxes'] for point in box]
            left, top = min(point[0] for point in points), min(point[1] for point in points)
            right, bottom = max(point[0] for point in points), max(point[1] for point in points)
            return (left + controller.b_region[0], top + controller.b_region[1], right - left, bottom - top)

        checks['invoice_match_annotation'] = len(callouts) == 2 and all(
            card['name'] in fields_by_name
            and card['a_text'] == fields_by_name[card['name']]['result']['text']
            and card['status'] == 'matched'
            and tuple(round(value * dpr) for value in card['anchor'])
                == selected_bounds(fields_by_name[card['name']])
            and card['rect'][1] >= card['anchor'][1] + card['anchor'][3]
            and len(card['display_lines']) == 1
            and fields_by_name[card['name']]['result']['text'] in card['display_lines'][0]
            and '100%' in card['display_lines'][0]
            for card in callouts)
        pin_ok = not bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        for pinned in (False, True):
            window.set_pinned(pinned)
            pin_ok &= (bool(window.highlight_overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == pinned
                       and not bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        checks['annotation_pin_persistence'] = bool(pin_ok)
        # The controller clears outdated marks at the start of a new cycle.
        # Exercise the valid same-epoch hook contract while current A annotations
        # are visible as well, using the real cropper and synthetic MSS pixels.
        if not before():
            raise ValueError('Visible callout guard refused')
        try:
            regions = {field['id']: field['region'] for field in controller.fields}
            regions['B'] = controller.b_region
            capture_module.capture_shared(regions)
        finally:
            after()

        controller.pause()
        pixels['b'] = 201
        duplicate = ready(controller.start)['fields'][0]['result']
        checks['ambiguous_requires_selection'] = (duplicate['status'] == 'ambiguous'
            and duplicate['selected_id'] is None and len(duplicate['candidates']) == 2)
        choice = duplicate['candidates'][1]['id']
        selected = ready(lambda: controller.select_candidate(first, choice))['fields'][0]['result']
        checks['manual_selection_applied'] = selected['status'] == 'matched' and selected['selected_id'] == choice

        previous = {field['id']: field['region'] for field in controller.fields}
        shifted = ready(lambda: controller.move_rows(1))
        checks['manual_row_shift'] = all(field['region'][1] == previous[field['id']][1] + 20
                                         for field in shifted['fields'])
        controller.pause()
        pixels['a60'], pixels['b'] = (16, 15), 202
        scrolled = ready(controller.start)
        checks['same_y_scroll_refreshed'] = (scrolled['fields'][0]['region'][1] == 60
            and scrolled['fields'][0]['result']['text'] == '新捲動資料')

        controller.pause()
        pixels['a60'] = (17, 15)
        count = len(snapshots)
        assert controller.start()
        _until(entered.is_set)
        pixels['a60'], pixels['b'] = (18, 15), 204
        release.set()
        _until(lambda: len(snapshots) > count)
        checks['stale_rows_discarded'] = ('changed' in states
            and snapshots[-1]['fields'][0]['result']['text'] == '目前列'
            and not any(field['result']['text'] == '過期列'
                        for snap in snapshots for field in snap['fields']))

        # Dates and near numbers travel through the same capture, worker and
        # verified publication path as normal invoice results.
        controller.pause()
        pixels['a60'], pixels['b'] = (20, 21), 206
        invoice = ready(controller.start)['fields']
        near, date = invoice[0]['result'], invoice[1]['result']
        near_candidate = next(row for row in near['candidates'] if row['id'] == near['selected_id'])
        date_candidate = next(row for row in date['candidates'] if row['id'] == date['selected_id'])
        chosen = ready(lambda: controller.select_candidate(first, near_candidate['id']))['fields'][0]['result']
        checks['numeric_similarity_review'] = (
            near['status'] == chosen['status'] == 'uncertain'
            and near_candidate['match_type'] == 'similar' and near_candidate['score'] == .8
            and near_candidate['needs_review'] is True)
        checks['roc_gregorian_date_equivalence'] = (
            date['status'] == 'matched' and date_candidate['match_type'] == 'date_equivalent'
            and date_candidate['score'] == 1.0 and date['text'] == '民國115年10月12日')

        from PySide6.QtGui import QImage
        opacity_ok = bool(window.highlight_overlay.callouts)
        if opacity_ok:
            rect = window.highlight_overlay.callouts[0]['rect']
            point = (int(rect[0]) + 1, int(rect[1]) + 1)
            for percent in (0, 50, 100):
                window._background_transparency.setValue(100 - percent)
                rendered = QImage(window.highlight_overlay.size(), QImage.Format.Format_ARGB32)
                rendered.fill(0)
                window.highlight_overlay.render(rendered)
                opacity_ok &= rendered.pixelColor(*point).alpha() == round(255 * percent / 100)
            window._background_transparency.setValue(100)
        checks['annotation_background_opacity'] = bool(opacity_ok)

        old_inferences, previous_snapshots = len(calls), len(snapshots)
        _until(lambda: len(snapshots) > previous_snapshots)
        unchanged_inferences = len(calls) == old_inferences and snapshots[-1]['state'] == 'unchanged'
        old_b_calls = calls.count(206)
        ready(lambda: window.manual_button.click())
        checks['manual_refresh_rechecks_unchanged_invoice'] = (
            unchanged_inferences and calls.count(206) == old_b_calls + 1
            and window.manual_button.isVisible() and not window.isVisible())
        checks['capture_overlay_hidden'] = (any(item[0] for item in hide_observations)
            and all(item[1] and item[2] for item in hide_observations) and bool(captures))
        captured, result_count = len(captures), len(snapshots)
        window.close()
        _wait(1100)
        checks['close_stops_polling'] = (not controller.running and not window.isVisible()
            and len(captures) == captured and len(snapshots) == result_count
            and not any(timer.isActive() for timer in
                        (controller._poll, controller._settle, controller._retry, controller._deadline)))
        checks['clipboard_history_untouched'] = app.clipboard().text() == clipboard and not history
    finally:
        release.set()
        checks['workers_stopped'] = _close_resources(window, controller, worker)
        capture_module.mss.mss = screen_factory
    tracking = _tracking_checks(app)
    checks['blue_calibration'], checks['blue_follow_stable'] = tracking
    for name, passed in _row_region_checks(app).items():
        if name in ('workers_stopped', 'clipboard_history_untouched'):
            checks[name] &= passed
        else:
            checks[name] = passed
    return checks


def _row_region_checks(app):
    """Read a synthetic source region through real capture/OCR queues and UI."""
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    from .comparison import shared_capture as capture_module
    from .comparison.shared_controller import SharedSearchController
    from .ui.shared_search_window import SharedSearchWindow
    from .workers.ocr_worker import OcrWorker

    names = ('source_region_rows_extracted', 'row_values_exclusive_invoice_positions',
             'near_candidates_visible_for_review', 'source_row_arrow_navigation',
             'changed_invoice_refreshes_matches')
    checks = dict.fromkeys(names, False)
    factory = capture_module.mss.mss
    window = controller = worker = None
    hidden = Event()
    pixels = {'invoice': 200}
    calls, snapshots, history, captures = [], [], [], []
    clipboard = app.clipboard().text()
    source_region, invoice_region = (30, 30, 330, 200, 1), (400, 20, 350, 650, 1)

    def line(text, x, y, width=60):
        return {'text': text, 'confidence': 1.,
                'box': [[x, y], [x + width, y], [x + width, y + 12], [x, y + 12]]}

    source_lines = [line('12345', 5, 20), line('12345', 95, 20), line('67890', 185, 20, 70),
                    line('54321', 5, 90), line('13579', 95, 90)]

    class Screen:
        monitors = ({'left': 0, 'top': 0, 'width': 1200, 'height': 1000},) * 2
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def grab(self, region):
            if not hidden.is_set():
                raise ValueError('Row capture preceded overlay hide')
            captures.append(dict(region))
            canvas = np.full((1000, 1200, 4), 25, np.uint8)
            canvas[:, :, 3] = 255
            canvas[30:230, 30:360, :3] = 31
            canvas[20:670, 400:750, :3] = pixels['invoice']
            x, y, width, height = (region[key] for key in ('left', 'top', 'width', 'height'))
            return canvas[y:y + height, x:x + width].copy()

    class Oracle:
        def set_progress_callback(self, callback):
            pass
        def is_ready(self):
            return True
        def run_ocr(self, image, mode):
            code = int(image[0, 0, 0])
            calls.append(code)
            if code == 31:
                rows = source_lines
            elif code in (200, 201):
                offset = 20 if code == 201 else 0
                rows = [line(text, 10, 20 + index * 100 + offset, 150)
                        for index, text in enumerate(('12345', '67891', '54321', '13579'))]
            else:
                raise ValueError('Unexpected synthetic crop')
            return {'text': '\n'.join(row['text'] for row in rows), 'status': 'done', 'detail': rows}

    def ready(action):
        count = len(snapshots)
        if action() is False:
            raise ValueError('Row action refused')
        _until(lambda: len(snapshots) > count or not controller.running)
        if len(snapshots) <= count or snapshots[-1]['state'] != 'ready':
            raise ValueError('Row results were not published after verification')
        return snapshots[-1]

    def chosen(field):
        result = field.get('result') or {}
        return next((candidate for candidate in result.get('candidates', [])
                     if candidate['id'] == result.get('selected_id')), None)

    def arrow_matches(snapshot):
        region = snapshot['active_row_region']
        polygon = window.highlight_overlay.source_row_arrow
        if not region or not polygon or window.highlight_overlay.source_row != tuple(region[:4]):
            return False
        scale = app.primaryScreen().devicePixelRatio()
        rectangle = QPolygonF([QPointF(*point) for point in polygon]).boundingRect()
        return (abs(rectangle.center().y() * scale - (region[1] + region[3] / 2)) < .01
                and rectangle.right() * scale <= region[0]
                and window.highlight_overlay.isVisible())

    try:
        capture_module.mss.mss = Screen
        worker = OcrWorker(Oracle())
        capture = capture_module.SharedCaptureWorker(capture=capture_module.capture_shared)
        controller = SharedSearchController(worker, capture_worker=capture)
        window = SharedSearchWindow(controller)  # Exercise the actual default rows mode.
        def before():
            accepted = window.before_capture()
            if accepted and not window.highlight_overlay.isVisible():
                hidden.set()
            return accepted
        def after():
            window.after_capture()
            hidden.clear()
        controller.before_capture, controller.after_capture = before, after
        controller.updated.connect(snapshots.append)
        worker.ocr_done.connect(lambda *values: history.append(values))
        assert controller.source_mode == window._source_mode.currentData() == 'rows'
        assert window.set_region('B', invoice_region)
        assert window.set_region('SOURCE', source_region)
        controller.set_interval(1)
        initial = ready(window._start_button.click)
        checks['source_region_rows_extracted'] = (
            initial['source_mode'] == 'rows' and initial['current_row_index'] == 0
            and [row['value_count'] for row in initial['source_rows']] == [3, 2]
            and [row['region'] for row in initial['source_rows']] == [(35, 50, 250, 12, 1), (35, 120, 150, 12, 1)]
            and [field['result']['text'] for field in initial['fields']] == ['12345', '12345', '67890']
            and calls == [200, 31] and not window.isVisible())
        selections = [chosen(field) for field in initial['fields']]
        selected_boxes = [tuple(tuple(tuple(point) for point in box) for box in candidate['boxes'])
                          for candidate in selections if candidate is not None]
        checks['row_values_exclusive_invoice_positions'] = (
            len(selections) == 3 and len(selected_boxes) == len(set(selected_boxes)) == 2
            and sum(initial['fields'][index]['result']['status'] == 'matched' for index in (0, 1)) == 1
            and sum(selections[index] is None for index in (0, 1)) == 1
            and any(initial['fields'][index]['result']['status'] == 'uncertain'
                    and selections[index] is None for index in (0, 1)))
        near = initial['fields'][2]['result']
        near_cards = [card for card in window.highlight_overlay.callouts if card['a_text'] == '67890']
        checks['near_candidates_visible_for_review'] = (
            near['status'] == 'uncertain' and near['score'] == .8
            and selections[2] is not None and selections[2]['match_type'] == 'similar'
            and selections[2]['needs_review'] is True and len(near_cards) == 1
            and near_cards[0]['status'] == 'similar'
            and '80%' in near_cards[0]['display_lines'][0] and '100%' not in near_cards[0]['display_lines'][0])
        first_arrow = window.highlight_overlay.source_row_arrow
        navigation_ok = arrow_matches(initial) and window._next.isEnabled() and not window._previous.isEnabled()
        second = ready(window._next.click)
        navigation_ok &= (second['current_row_index'] == 1 and arrow_matches(second)
                          and window.highlight_overlay.source_row_arrow != first_arrow
                          and [field['result']['text'] for field in second['fields']] == ['54321', '13579']
                          and window._previous.isEnabled() and not window._next.isEnabled()
                          and controller.source_region == source_region and controller.b_region == invoice_region
                          and calls == [200, 31, 31])
        back = ready(window._previous.click)
        navigation_ok &= (back['current_row_index'] == 0 and arrow_matches(back)
                          and window.highlight_overlay.source_row_arrow == first_arrow
                          and controller.source_region == source_region and controller.b_region == invoice_region
                          and calls == [200, 31, 31, 31])
        checks['source_row_arrow_navigation'] = bool(navigation_ok)
        old_selected = {field['id']: chosen(field)['boxes'] for field in back['fields'] if chosen(field) is not None}
        pixels['invoice'] = 201
        changed = ready(lambda: None)  # The real poll must notice pixels changing inside the same ROI.
        new_selected = {field['id']: chosen(field) for field in changed['fields'] if chosen(field) is not None}
        changed_ok = (controller.b_region == invoice_region and controller.source_region == source_region
                      and changed['current_row_index'] == 0 and calls.count(201) == 1
                      and calls.count(31) == 4 and arrow_matches(changed)
                      and set(new_selected) == set(old_selected)
                      and all(new_selected[key]['boxes'] != old
                              and new_selected[key]['boxes'][0][0][1] == old[0][0][1] + 20
                              for key, old in old_selected.items()))
        before_manual = (calls.count(201), calls.count(31))
        manual = ready(window.manual_button.click)
        checks['changed_invoice_refreshes_matches'] = bool(
            changed_ok and calls.count(201) == before_manual[0] + 1
            and calls.count(31) == before_manual[1] + 1 and manual['current_row_index'] == 0
            and {field['id']: chosen(field)['boxes'] for field in manual['fields'] if chosen(field) is not None}
                == {key: candidate['boxes'] for key, candidate in new_selected.items()})
        checks['clipboard_history_untouched'] = app.clipboard().text() == clipboard and not history and bool(captures)
    finally:
        checks['workers_stopped'] = _close_resources(window, controller, worker)
        capture_module.mss.mss = factory
    return checks


def _tracking_checks(app):
    import numpy as np
    from .comparison import shared_capture as capture_module
    from .comparison.shared_controller import SharedSearchController
    from .ui.shared_search_window import SharedSearchWindow
    from .workers.ocr_worker import OcrWorker

    factory = capture_module.mss.mss
    window = controller = worker = None
    frame = {'y': 40, 'codes': (31, 32)}
    snapshots, requests, calls, states = [], [], [], []
    class Screen:
        monitors = ({'left': 0, 'top': 0, 'width': 400, 'height': 240},) * 2
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def grab(self, region):
            canvas = np.full((240, 400, 4), 25, np.uint8)
            canvas[:, :, 3] = 255
            y = frame['y']
            canvas[y:y + 12, :180, :3] = (210, 100, 25)
            for x, value in zip((20, 100), frame['codes']):
                canvas[y:y + 3, x:x + 3, :3] = value
            canvas[20:180, 230:380, :3] = 200
            x, top, w, h = (region[key] for key in ('left', 'top', 'width', 'height'))
            return canvas[top:top + h, x:x + w].copy()
    class Oracle:
        def set_progress_callback(self, callback):
            pass
        def is_ready(self):
            return True
        def run_ocr(self, image, mode):
            value = int(image[0, 0, 0])
            calls.append(value)
            values = {31: '列一甲', 32: '列一乙', 41: '列二甲', 42: '列二乙', 51: '新捲動甲', 52: '新捲動乙'}
            if value == 200:
                return {'text': '\n'.join(values.values()), 'status': 'done',
                        'detail': [_row(text, 5 + i * 23) for i, text in enumerate(values.values())]}
            return {'text': values[value], 'status': 'done', 'detail': []}
    try:
        capture_module.mss.mss = Screen
        def capture(regions, layout):
            requests.append((frame['y'], dict(regions)))
            return capture_module.capture_shared(regions, layout)
        worker = OcrWorker(Oracle())
        controller = SharedSearchController(worker, capture_worker=capture_module.SharedCaptureWorker(capture=capture))
        window = SharedSearchWindow(controller)
        window._source_mode.setCurrentIndex(window._source_mode.findData('fields'))
        controller.updated.connect(lambda value: snapshots.append(value))
        controller.state_changed.connect(states.append)
        first = controller.fields[0]['id']
        second = controller.add_field('第二欄')
        assert second is not None
        assert controller.set_a_region(first, (20, 40, 50, 12, 1))
        assert controller.set_a_region(second, (100, 40, 50, 12, 1))
        assert controller.set_b_region((230, 20, 150, 160, 1))
        assert controller.set_tracking_region((0, 20, 180, 160, 1))
        assert controller.set_row_pitch(20)
        assert controller.calibrate_tracking()
        _until(lambda: controller.calibrated and not controller.calibrating)
        calibrated = controller.calibrated
        assert controller.set_auto_follow(True)
        controller.set_interval(1)
        assert controller.start()
        _until(lambda: bool(snapshots))
        if any(field['result']['status'] != 'matched' for field in snapshots[-1]['fields']):
            raise ValueError('Initial calibrated row did not match')
        frame['y'], frame['codes'] = 60, (41, 42)
        _until(lambda: len(snapshots) >= 2)
        moved = snapshots[-1]
        translated = all(field['region'][1] == 60 for field in moved['fields'])
        new_row_requests = [regions for y, regions in requests if y == 60 and first in regions]
        first_moved = next((i for i, regions in enumerate(new_row_requests) if regions[first][1] == 60), -1)
        stable = 'tracking_pending' in states and first_moved >= 2
        frame['codes'] = (51, 52)  # Same selected-row Y; only its content changes.
        _until(lambda: len(snapshots) >= 3)
        refreshed = snapshots[-1]
        return calibrated, bool(stable and translated
            and refreshed['fields'][0]['region'][1] == 60
            and refreshed['fields'][0]['result']['text'] == '新捲動甲'
            and calls.count(200) == 1)
    finally:
        stopped = _close_resources(window, controller, worker)
        capture_module.mss.mss = factory
        if not stopped:
            raise ValueError('Tracking workers did not stop')


def run_shared_search_probe(report_path):
    from PySide6.QtWidgets import QApplication
    from .core.validation_report import executable_sha256, write_validation_report
    from .core.version import APP_VERSION

    app = QApplication.instance() or QApplication([])
    previous_quit_on_close = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    report = dict(schema=1, probe='shared_search_lifecycle', version=APP_VERSION,
        platform=sys.platform, frozen=bool(getattr(sys, 'frozen', False)),
        qt_platform=app.platformName(), executable_sha256=None,
        clean_machine_verified=False, passed=False, fixture_kind='SYNTHETIC_SHARED_SEARCH',
        ocr_accuracy_tested=False, model_inference_tested=False, overlay_capture_verified=False,
        checks=dict.fromkeys(CHECK_NAMES, False))
    commit = os.environ.get('GITHUB_SHA', '')
    if re.fullmatch('[0-9a-f]{40}', commit):
        report['source_commit'] = commit
    try:
        write_validation_report(report_path, report)
        report['executable_sha256'] = executable_sha256()
    except (OSError, ValueError, TypeError):
        app.setQuitOnLastWindowClosed(previous_quit_on_close)
        return 1
    previous_home = os.environ.get('DESKTOP_OCR_HOME')
    previous_clipboard = app.clipboard().text()
    temporary = None
    try:
        with tempfile.TemporaryDirectory(prefix='shared-search-probe-') as directory:
            temporary = Path(directory)
            os.environ['DESKTOP_OCR_HOME'] = directory
            report['checks'].update(_logical_checks(app))
            if sys.platform == 'win32':
                from .shared_search_windows_probe import verify_overlay_capture
                report['overlay_capture_verified'] = verify_overlay_capture(app)
            report['checks']['clipboard_history_untouched'] &= app.clipboard().text() == previous_clipboard
            report['checks']['temporary_files_cleaned'] = not any(temporary.iterdir())
        report['checks']['temporary_files_cleaned'] &= not temporary.exists()
        report['passed'] = all(report['checks'].values()) and (
            sys.platform != 'win32' or report['overlay_capture_verified'] is True)
    except Exception:  # noqa: BLE001 - never export pixels, recognized strings or private diagnostics
        report['passed'] = False
    finally:
        app.setQuitOnLastWindowClosed(previous_quit_on_close)
        if previous_home is None:
            os.environ.pop('DESKTOP_OCR_HOME', None)
        else:
            os.environ['DESKTOP_OCR_HOME'] = previous_home
    if not report['passed']:
        report['error_code'] = 'SHARED_SEARCH_PROBE_CHECK_FAILED'
    try:
        write_validation_report(report_path, report)
    except (OSError, ValueError, TypeError):
        return 1
    return 0 if report['passed'] else 1
