# OCR 修復驗證與模型處置

本次權重 SHA-256 不變。`models.lock.json` 對應實際 RapidOCR kwargs，載入前驗證 det／rec／cls；DTO 保存實際 engine 及三個模型 hashes。放大後的 box 在輸出前轉回原始圖像座標。Optional provider 未宣稱已通過 native Windows 整合。

## 空格 Ground Truth

`tests/test_hardening.py` 的 table 涵蓋 CJK↔CJK、CJK↔Latin、Latin↔Latin、number↔unit、百分比、日期、金額、括號及欄位；保留框內原有空白。框距大於較小框高的 0.75 倍保留分隔。兩段數字一律保守分開，避免帳號與數量黏合。日期斜線、金額逗點、小數點與貨幣符號可在相鄰框重建。

初版把近距離數字直接相接，在既有 mixed-language GT 重播發現 `12345678 10` 被黏成 `1234567810`，已修正並加入負例。這是實際回退證據，不能只靠正常案例測試。

24 張小字／低對比合成圖的 frozen first-pass hypotheses 重播：624 個 GT 字元，前後 CER 都為 **38.46%**。S/D/I 從 19/212/9 變為 19/213/8；whitespace edit 前後都是 27。個別案例的錯誤分布有變化，**不宣稱整體 OCR 準確度提升**。Detection 與 recognizer 沒有在這個重播重新執行，這個 CER 不能取代 cropped Recognition Accuracy。

```shell
python scripts/replay_spacing.py --baseline docs/evidence/spacing-replay-input.json --report spacing-replay.json
```

## 二次辨識融合

`fuse_passes` 只比較同一 recognizer 兩次推論。跨 pass 的框交集／較小面積 >= 0.6 建立 overlap component；每個 component 選一組完整 hypothesis，避免整行與碎片同時保留。候選總面積低於原本 80% 時，不用高信心局部文字取代完整行；其餘以字數加權 confidence 仲裁。相同字出現在不同位置不會被文字去重刪掉。

已測同 glyph 重複、不同字高信心候選、整行／碎片碰撞、相同字不同位置、高信心局部 crop、原圖座標回映。AABB 對旋轉文字／表格仍有限制；confidence 不是正確率，跨引擎 confidence 尚未校準。本次沒有宣稱所有融合 regression 已解決。

## 模型更新

[Phase 2 模型決策](model-benchmark/MODEL_UPGRADE_DECISION.md)及[結果](model-benchmark/PHASE2_RESULTS.md)保留配對測量。v4 detector＋v6 recognizer 是後續整合候選，公開 cropped CER 5.88%→1.66%，但包含 12 個原本完全正確的 crop 回退，罕字 CER 仍為47.92%，小字漏偵測保留。這些數值屬 Phase 2 corpus，不能當目前候選应用的全流程指標。

台灣 custom dictionary 仍未接入 Runtime。沒有加入全域 `己→已` 替換。Domain lexicon／人名／稅務／勞健保／法律詞彙修正須保留原文、候選、context score 與修改來源，先取得正確文字負例的 wrong-correction gate。
