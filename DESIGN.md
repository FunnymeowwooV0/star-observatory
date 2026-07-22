---
product: 觀星台
platform: static-responsive-web
audience: 個人每日掃描開源與 AI 趨勢，未來公開展示
direction: 深色精密資料儀表板
colors:
  light:
    background: "#f7f7f4"
    surface: "#ffffff"
    text: "#1a1a19"
    muted: "#6b6b66"
    border: "#e3e2db"
    accent: "#0f6e56"
    danger: "#b23b3b"
    info: "#185fa5"
  dark:
    background: "#161615"
    surface: "#1f1f1d"
    text: "#f2f2ee"
    muted: "#a3a29a"
    border: "#2f2f2c"
    accent: "#5dcaa5"
    danger: "#ff8f8f"
    info: "#85b7ff"
typography:
  display: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang TC, Microsoft JhengHei, sans-serif"
  workhorse: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang TC, Microsoft JhengHei, sans-serif"
  data: "ui-monospace, SFMono-Regular, Menlo, monospace"
  weights: [400, 700]
spacing: [4, 8, 12, 16, 24, 32, 48, 64, 96]
radius:
  small: 4
  medium: 8
  large: 12
motion:
  duration_ms: 140
  easing: "cubic-bezier(0.2, 0, 0, 1)"
  card_hover: "translateY(-2px) scale(1.005)"
  card_active: "translateY(0) scale(0.995)"
---

# 觀星台設計正本

## 設計理念

觀星台是快速掃讀用的資料工具，不是行銷落地頁。畫面要像精密但親切的觀測儀器：資訊層級明確、數字先被看見、點擊目標直覺，視覺裝飾不得搶走資料。

既有明暗配色、薄荷綠強調色與系統中文字體維持不變。本輪只改善內容結構與互動，不另開 Task B 品牌改造。

## 色彩使用規則

- 薄荷綠只用於主要選取狀態、今日新增量與焦點框，不大面積鋪滿。
- 紅／藍只表達排名下降／新進榜；資訊不能只靠顏色，必須同時有文字或符號。
- 卡片用實色表面與細邊框，不使用玻璃、霓虹或紫藍漸層。

## 元件規則

### 榜單分頁

- 「今日榜／累積榜」是同層級分頁，一次只顯示一個面板。
- 分頁列在頁首下方並可黏住；歷史日期選擇器同列但屬次要控制。
- 支援滑鼠、鍵盤左右鍵、網址 hash、上一頁／下一頁；無 JavaScript 時退化為頁內錨點。

### GitHub 大卡

- 桌面兩欄、手機一欄，共十張；整張 `<a>` 是唯一主要連結。
- 內容順序：排名／用途分類／昨日變化 → repo 名 → 說明 → 今日新增星 → 總星與語言。
- 今日新增星是視覺主數字，總星與語言是輔助資料。
- 不放箭頭、不放「GitHub 倉庫」小字、不放無說明的迷你趨勢圖。

### 長條連結卡

- HF、OpenRouter、Product Hunt、Ollama 與累積榜的單一目的地項目，整列皆可點。
- HN 有原文與討論兩個目的地：大卡進原文，另給足 44px 的「HN 討論」控制。
- 連結感由游標、邊框／背景反應與微位移表達，不用大箭頭。

### 動效

- 滑鼠移入卡片：140ms 微上浮 2px、放大 0.5%、邊框提亮。
- 點下：縮回 0.5%；鍵盤焦點使用 3px 清楚外框。
- `prefers-reduced-motion: reduce` 時取消位移與縮放。

## 產品絕對不會做的事

- 不用未標示的小圖表、假 KPI 或裝飾性資料。
- 不把小字連結藏在卡片底部要求精準點擊。
- 不用 AI 猜分類卻假裝是 GitHub 官方資料；用途標籤必須註明為規則推定。
- 不新增前端框架、後端或資料 schema 來完成單頁互動。
- 不在本輪改品牌配色、字體或發佈設定。
