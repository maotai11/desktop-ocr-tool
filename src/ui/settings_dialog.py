# -*- coding: utf-8 -*-
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget,
    QWidget, QLabel, QCheckBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QPushButton, QFormLayout,
    QDialogButtonBox, QComboBox, QGroupBox
)

from .theme import (
    _BG, _BG_RAISE, _BG_HOVER, _BORDER,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T,
    _ACCENT_15, _ACCENT_18, _ACCENT_35,
)

logger = logging.getLogger(__name__)

_DIALOG_QSS = f"""
    QDialog {{
        background: {_BG};
    }}
    QWidget {{
        background: {_BG};
        color: {_TEXT_PRI};
        font-size: 13px;
    }}
    QTabWidget::pane {{
        border: 1px solid {_BORDER};
        border-radius: 5px;
        background: {_BG};
    }}
    QTabBar::tab {{
        background: {_BG_RAISE};
        color: {_TEXT_SEC};
        padding: 6px 16px;
        border: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {_ACCENT_15};
        color: {_ACCENT};
        border-bottom: 2px solid {_ACCENT};
    }}
    QTabBar::tab:hover:!selected {{
        background: {_BG_HOVER};
        color: {_TEXT_PRI};
    }}
    QLabel {{
        color: {_TEXT_PRI};
        background: transparent;
    }}
    QCheckBox {{
        color: {_TEXT_PRI};
        background: transparent;
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 15px;
        height: 15px;
        border-radius: 3px;
        border: 1px solid {_BORDER};
        background: {_BG_RAISE};
    }}
    QCheckBox::indicator:checked {{
        background: {_ACCENT};
        border: 1px solid {_ACCENT};
        image: none;
    }}
    QCheckBox::indicator:hover {{
        border: 1px solid {_ACCENT};
    }}
    QSpinBox {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QSpinBox:focus {{
        border: 1px solid {_ACCENT};
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background: {_BG_HOVER};
        border: none;
        width: 16px;
    }}
    QLineEdit {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 3px 8px;
    }}
    QLineEdit:focus {{
        border: 1px solid {_ACCENT};
    }}
    QComboBox {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 3px 8px;
        min-width: 100px;
    }}
    QComboBox:focus {{
        border: 1px solid {_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        selection-background-color: {_ACCENT_18};
        selection-color: {_ACCENT};
    }}
    QDialogButtonBox QPushButton {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 5px 16px;
        min-width: 64px;
    }}
    QDialogButtonBox QPushButton:hover {{
        background: {_BG_HOVER};
        border: 1px solid {_ACCENT};
        color: {_ACCENT};
    }}
    QDialogButtonBox QPushButton[text="OK"],
    QDialogButtonBox QPushButton[text="確定"] {{
        background: {_ACCENT};
        color: {_ACCENT_T};
        border: none;
        font-weight: 600;
    }}
    QDialogButtonBox QPushButton[text="OK"]:hover,
    QDialogButtonBox QPushButton[text="確定"]:hover {{
        background: {_ACCENT_H};
        color: {_ACCENT_T};
    }}
"""


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None, ocr_engine=None):
        super().__init__(parent)
        self._cfg = cfg
        self._ocr_engine = ocr_engine
        self.setWindowTitle("設定")
        self.setMinimumSize(520, 420)
        self._setup_ui()
        self.setStyleSheet(_DIALOG_QSS)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- 一般 ----
        gen = QWidget()
        gf = QFormLayout(gen)
        gf.setSpacing(10)
        self._cb_autostart = QCheckBox("開機自動啟動")
        self._cb_autostart.setChecked(
            self._cfg.get('general', 'start_with_windows', default=False)
        )
        gf.addRow("系統啟動：", self._cb_autostart)

        self._cb_start_min = QCheckBox("啟動時最小化到系統匣")
        self._cb_start_min.setChecked(
            self._cfg.get('general', 'start_minimized', default=True)
        )
        gf.addRow("", self._cb_start_min)
        tabs.addTab(gen, "一般")

        # ---- 擷取 ----
        cap = QWidget()
        cf = QFormLayout(cap)
        cf.setSpacing(10)
        self._cb_auto_ocr = QCheckBox("截圖後自動執行 OCR")
        self._cb_auto_ocr.setChecked(
            self._cfg.get('capture', 'auto_ocr_on_capture', default=True)
        )
        cf.addRow("OCR：", self._cb_auto_ocr)
        tabs.addTab(cap, "擷取")

        # ---- OCR ----
        ocr_tab = QWidget()
        of = QFormLayout(ocr_tab)
        of.setSpacing(10)

        self._cb_second_pass = QCheckBox("空結果或低信心時自動二次辨識")
        self._cb_second_pass.setChecked(
            self._cfg.get('ocr', 'enable_second_pass', default=True)
        )
        of.addRow("二次辨識：", self._cb_second_pass)

        self._cb_handwriting = QCheckBox("手寫友善模式（積極前處理）")
        self._cb_handwriting.setChecked(
            self._cfg.get('ocr', 'enable_handwriting_mode', default=False)
        )
        of.addRow("手寫模式：", self._cb_handwriting)

        self._sp_short_side = QSpinBox()
        self._sp_short_side.setRange(64, 2048)
        self._sp_short_side.setSingleStep(64)
        self._sp_short_side.setSuffix(" px")
        self._sp_short_side.setValue(
            self._cfg.get('ocr', 'max_image_short_side', default=960)
        )
        of.addRow("小圖放大門檻：", self._sp_short_side)

        _ocr_note = QLabel("以上設定儲存後即時生效，無需重啟。")
        _ocr_note.setStyleSheet(f"color: {_TEXT_SEC}; font-size: 11px;")
        of.addRow("", _ocr_note)

        # ---- OCR tab：第二引擎備援區塊（Patch H3）----
        sec_box = QGroupBox("第二引擎備援（選用套件）")
        sec_box.setStyleSheet(f"""
            QGroupBox {{
                color: {_TEXT_SEC};
                font-size: 12px;
                border: 1px solid {_BORDER};
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: {_TEXT_SEC};
            }}
        """)
        sec_layout = QFormLayout(sec_box)
        sec_layout.setSpacing(8)

        self._sec_enabled = QCheckBox("啟用第二引擎備援辨識")
        self._sec_enabled.setChecked(
            self._cfg.get('ocr', 'enable_secondary_engine', default=False)
        )
        sec_layout.addRow("備援：", self._sec_enabled)

        self._sec_provider = QComboBox()
        # 僅顯示已登記的 provider（不一定安裝）
        try:
            from ..ocr.providers import list_known_providers
            for p in list_known_providers():
                self._sec_provider.addItem(p, userData=p)
        except Exception:
            self._sec_provider.addItem('paddleocr_v5_mobile', userData='paddleocr_v5_mobile')
        saved_provider = self._cfg.get(
            'ocr', 'secondary_engine_provider', default='paddleocr_v5_mobile'
        )
        idx = self._sec_provider.findData(saved_provider)
        if idx >= 0:
            self._sec_provider.setCurrentIndex(idx)
        sec_layout.addRow("Provider：", self._sec_provider)

        self._sec_diag_label = QLabel()
        self._sec_diag_label.setStyleSheet("font-size: 11px;")
        self._update_sec_diag()
        self._sec_provider.currentIndexChanged.connect(self._update_sec_diag)
        sec_layout.addRow("安裝狀態：", self._sec_diag_label)

        self._sec_handwriting = QCheckBox("手寫模式時使用第二引擎")
        self._sec_handwriting.setChecked(
            self._cfg.get('ocr', 'secondary_engine_for_handwriting', default=True)
        )
        sec_layout.addRow("手寫備援：", self._sec_handwriting)

        self._sec_low_conf = QCheckBox("低信心時使用第二引擎")
        self._sec_low_conf.setChecked(
            self._cfg.get('ocr', 'secondary_engine_for_low_confidence', default=True)
        )
        sec_layout.addRow("低信心備援：", self._sec_low_conf)

        self._sec_threshold = QDoubleSpinBox()
        self._sec_threshold.setRange(0.01, 1.00)
        self._sec_threshold.setSingleStep(0.05)
        self._sec_threshold.setDecimals(2)
        self._sec_threshold.setValue(
            self._cfg.get('ocr', 'secondary_engine_confidence_threshold', default=0.85)
        )
        _threshold_note = QLabel("主引擎信心低於此值時，啟用第二引擎（低信心備援）")
        _threshold_note.setStyleSheet(f"color: {_TEXT_SEC}; font-size: 11px;")
        sec_layout.addRow("信心門檻：", self._sec_threshold)
        sec_layout.addRow("", _threshold_note)

        of.addRow(sec_box)
        tabs.addTab(ocr_tab, "OCR")

        # ---- 剪貼簿 ----
        clip = QWidget()
        clf = QFormLayout(clip)
        clf.setSpacing(10)
        self._cb_monitor = QCheckBox("啟用剪貼簿監聽")
        self._cb_monitor.setChecked(
            self._cfg.get('clipboard', 'monitor_clipboard', default=True)
        )
        clf.addRow("監聽：", self._cb_monitor)

        self._cb_auto_text = QCheckBox("自動收錄複製的文字")
        self._cb_auto_text.setChecked(
            self._cfg.get('clipboard', 'auto_save_text', default=True)
        )
        clf.addRow("文字：", self._cb_auto_text)

        self._cb_auto_image = QCheckBox("自動收錄剪貼簿圖片")
        self._cb_auto_image.setChecked(
            self._cfg.get('clipboard', 'auto_save_image', default=False)
        )
        clf.addRow("圖片：", self._cb_auto_image)

        dedup_lbl = QLabel("連續相同內容（60 秒內）自動去重")
        dedup_lbl.setStyleSheet(f"color: {_TEXT_SEC}; font-size: 11px;")
        clf.addRow("去重：", dedup_lbl)
        tabs.addTab(clip, "剪貼簿")

        # ---- 快捷鍵 ----
        hk_tab = QWidget()
        hkf = QFormLayout(hk_tab)
        hkf.setSpacing(10)
        hk_defs = self._cfg.get('hotkeys', default={})
        self._hk_edits = {}
        for key, lbl_text in [
            ('capture_region_ocr', 'OCR 框選'),
            ('capture_region_image', '截圖框選'),
            ('capture_fullscreen', '全螢幕截圖'),
            ('toggle_widget', '顯示/隱藏浮動窗'),
            ('open_console', '開啟主控台'),
            ('paste_last', '貼上最後一筆'),
        ]:
            edit = QLineEdit(hk_defs.get(key, ''))
            edit.setReadOnly(True)
            self._hk_edits[key] = edit
            hkf.addRow(f"{lbl_text}：", edit)
        tabs.addTab(hk_tab, "快捷鍵")

        # ---- 介面 ----
        ui_tab = QWidget()
        uf = QFormLayout(ui_tab)
        uf.setSpacing(10)
        self._cb_theme = QComboBox()
        self._cb_theme.addItems(["system", "light", "dark"])
        theme = self._cfg.get('ui', 'theme', default='system')
        idx = self._cb_theme.findText(theme)
        if idx >= 0:
            self._cb_theme.setCurrentIndex(idx)
        uf.addRow("佈景主題：", self._cb_theme)

        self._sp_font_size = QSpinBox()
        self._sp_font_size.setRange(9, 24)
        self._sp_font_size.setValue(self._cfg.get('ui', 'font_size', default=13))
        uf.addRow("字型大小：", self._sp_font_size)
        tabs.addTab(ui_tab, "介面")

        layout.addWidget(tabs)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._save)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _update_sec_diag(self):
        """Patch H3: 更新第二引擎安裝狀態標籤（不載入模型，僅做 import 檢查）。"""
        provider_name = self._sec_provider.currentData() or ''
        try:
            from ..ocr.providers import create_provider
            available = create_provider(provider_name).is_available()
        except Exception:
            available = False

        if available:
            self._sec_diag_label.setText("已安裝，可使用")
            self._sec_diag_label.setStyleSheet(f"color: #4caf50; font-size: 11px;")
        else:
            self._sec_diag_label.setText("未安裝（pip install paddleocr）")
            self._sec_diag_label.setStyleSheet(f"color: #ff9800; font-size: 11px;")

    def _save(self):
        old_theme = self._cfg.get('ui', 'theme', default='system')
        old_font_size = self._cfg.get('ui', 'font_size', default=13)

        self._cfg.set('general', 'start_with_windows', self._cb_autostart.isChecked())
        self._cfg.set('general', 'start_minimized', self._cb_start_min.isChecked())
        self._cfg.set('capture', 'auto_ocr_on_capture', self._cb_auto_ocr.isChecked())
        self._cfg.set('ocr', 'enable_second_pass', self._cb_second_pass.isChecked())
        self._cfg.set('ocr', 'enable_handwriting_mode', self._cb_handwriting.isChecked())
        self._cfg.set('ocr', 'max_image_short_side', self._sp_short_side.value())
        self._cfg.set('clipboard', 'monitor_clipboard', self._cb_monitor.isChecked())
        self._cfg.set('clipboard', 'auto_save_text', self._cb_auto_text.isChecked())
        self._cfg.set('clipboard', 'auto_save_image', self._cb_auto_image.isChecked())
        self._cfg.set('ui', 'theme', self._cb_theme.currentText())
        self._cfg.set('ui', 'font_size', self._sp_font_size.value())

        # ---- 第二引擎設定（Patch H3）----
        enable_sec = self._sec_enabled.isChecked()
        sec_provider_name = self._sec_provider.currentData() or 'paddleocr_v5_mobile'
        sec_threshold = self._sec_threshold.value()
        self._cfg.set('ocr', 'enable_secondary_engine', enable_sec)
        self._cfg.set('ocr', 'secondary_engine_provider', sec_provider_name)
        self._cfg.set('ocr', 'secondary_engine_for_handwriting',
                      self._sec_handwriting.isChecked())
        self._cfg.set('ocr', 'secondary_engine_for_low_confidence',
                      self._sec_low_conf.isChecked())
        self._cfg.set('ocr', 'secondary_engine_confidence_threshold', sec_threshold)

        # OCR 參數即時更新（不需重啟）
        if self._ocr_engine is not None:
            self._ocr_engine.configure(
                enable_second_pass=self._cb_second_pass.isChecked(),
                enable_handwriting_mode=self._cb_handwriting.isChecked(),
                max_image_short_side=self._sp_short_side.value(),
            )
            # 第二引擎即時切換（Patch H3）
            if enable_sec:
                from ..ocr.providers import create_provider
                provider = create_provider(
                    sec_provider_name, confidence_accept=sec_threshold
                )
                self._ocr_engine.set_secondary_engine(provider)
            else:
                from ..ocr.secondary_engine import NullSecondaryEngine
                self._ocr_engine.set_secondary_engine(NullSecondaryEngine())
            self._ocr_engine.configure(
                enable_secondary_engine=enable_sec,
                secondary_for_handwriting=self._sec_handwriting.isChecked(),
                secondary_for_low_confidence=self._sec_low_conf.isChecked(),
                secondary_confidence_threshold=sec_threshold,
            )

        from ..core.autostart import set_autostart
        set_autostart(self._cb_autostart.isChecked())

        ui_changed = (
            self._cb_theme.currentText() != old_theme or
            self._sp_font_size.value() != old_font_size
        )

        logger.info("設定已儲存")
        self.accept()  # 關閉 dialog 後再顯示提示，避免 dialog 關閉前彈出 QMessageBox 造成焦點閃動

        if ui_changed:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self.parent(), "設定已儲存",
                "佈景主題與字型大小的變更將在下次重新啟動後生效。"
            )
