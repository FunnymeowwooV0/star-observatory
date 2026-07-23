---
product: 觀星台
platform: static-responsive-web
audience: 個人每日掃描開源與 AI 趨勢，未來公開展示
direction: 銀河夕陽夜航編輯風
colors:
  sky_top: "#08206b"
  sky_mid: "#07184f"
  galaxy: "#1760e8"
  dusk_mauve: "#66506d"
  dusk_apricot: "#d7865b"
  surface: "#f2e9d2"
  surface_ink: "#111319"
  ivory: "#f5edda"
  star: "#e5c43a"
  muted_blue: "#9fb3de"
typography:
  display: "Noto Serif TC, serif"
  workhorse: "Noto Sans TC, sans-serif"
  data: "IBM Plex Mono, monospace"
  license: "SIL Open Font License 1.1"
spacing: [4, 8, 12, 16, 24, 32, 48, 64]
radius:
  small: 3
  medium: 6
  large: 8
motion:
  duration_ms: 140
  easing: "cubic-bezier(0.2, 0, 0, 1)"
  card_hover: "translateY(-2px) scale(1.005)"
  card_active: "translateY(0) scale(0.995)"
---

# 觀星台設計正本

## 設計理念

觀星台是快速掃讀用的資料工具，不是行銷落地頁。新版像一本攤開在夜空下的觀測誌：深鈷藍天空承載暖白資料頁，襯線標題建立編輯感，等寬數字讓排行仍可快速比較。

視覺正本是主理人於 2026-07-23 核准的「銀河夕陽版預覽」。預覽圖只作設計參考，不是網頁背景素材；實作使用純 CSS 漸層、顆粒與線條，避免下載大型圖片。

## 背景規則

- 主背景由深鈷藍起，亮區沿兩條不對稱、寬窄不一的曲線流動；不得退化成置中的規則 radial gradient。
- 銀河只表現為藍色光感，不加入寫實星雲、雲朵或密集銀河照片。
- 細顆粒覆蓋整個背景但不得壓低文字可讀性。
- 星點只出現在頁首至標題區高度，數量少，最多兩三顆帶微弱光暈；導覽列以下快速淡出。
- 頁面最下方由深藍經低飽和灰紫，慢慢過渡到杏橙／珊瑚夕陽色；不得變成亮橘色硬切帶。
- 右上與左下保留細薄橢圓軌道線，不放人物、地景、月球或參考網站徽章。

## 字體規則

- 標題與大型排名：`Noto Serif TC`。
- 介面與繁體中文內文：`Noto Sans TC`。
- repo 名、模型 ID、排名與數字：`IBM Plex Mono`。
- 三套字型皆使用 OFL 1.1 免費版本；網路字型載入失敗時必須退回本機繁中 serif／sans／monospace，不得造成版面跳壞。
- 襯線字只用於標題與大型數字，不把整頁內文變成書籍排版。

## 導覽規則

- 第一層為「今日榜／累積榜」；歷史日期選擇器維持同列的次要控制。
- 今日榜第二層固定順序：GitHub、HF 模型、HF 資料集、HF Spaces、Hacker News、OpenRouter、Product Hunt、Ollama；只顯示有對應面板的按鈕。
- HF 三類必須各自是獨立面板，不得包回單一 Hugging Face 長區塊。
- 累積榜第二層使用與今日榜同序的 8 個來源；每個來源內的第三層再切「本週／本月」。
- 切到累積榜時隱藏今日來源列，只顯示累積來源與該來源的期間列；切回今日榜時隱藏兩者。
- 手機第二層按鈕為單列橫向手指捲動；不換行、不放左右箭頭，也不得讓整個頁面產生水平捲動。
- 所有 tab 支援滑鼠、Enter／Space、左右鍵、Home／End、網址 hash、上一頁／下一頁與無 JavaScript 退化。
- 切換 tab 只更換當前面板與 hash，保留使用者當下的捲動位置；不可呼叫 `scrollIntoView()` 或自動往下滑。

## 榜單元件規則

### GitHub 大卡

- 桌面兩欄五列，手機單欄，共十張；整張 `<a>` 是唯一主要連結。
- 排名置左且使用襯線大字；repo、分類與簡介置中；總星與今日新增置右或在窄螢幕落到底列。
- 今日新增使用芥末黃；總星與語言維持次要層級。
- 卡面為暖白紙色、深墨字與細藍線；圓角克制，不使用浮誇陰影。
- 描述完整保留在 DOM，視覺最多兩行。

### 共用排行卡

- GitHub、HF、OpenRouter、Product Hunt、Ollama 的今日榜與累積榜共用同一排行卡結構：桌面兩欄、手機單欄。
- 每張卡都保留排名、名稱、來源／類型標籤與該榜單的主指標；有 URL 時整張卡為唯一主連結。
- HN 是唯一例外，維持長條雙入口：主列進原文，另給至少 44px 的「HN 討論」控制。

### 互動

- 不使用大箭頭、裝飾箭頭或要求精準點擊的小字連結。
- hover：140ms 微上浮 2px、放大 0.5%、邊線／底色輕微變化。
- active：縮回 0.5%；focus-visible 使用 3px 清楚外框。
- `prefers-reduced-motion: reduce` 時取消位移與 transition。

## 累積榜規則

- 刪除「🏆 累積排行榜」與同義的通用大標題。
- 累積榜的非 HN 項目必須沿用今日 GitHub 排行卡，不退回舊長條清單。
- 期間 tab 已說明「本週／本月」，面板只需直接顯示統計起日、天數與誠實限制，不再重複大標。
- 本週與本月不得同時出現在增強後畫面；無 JavaScript 時可依 DOM 順序全部閱讀。

## 產品絕對不會做的事

- 不用未標示的小圖表、假 KPI 或裝飾性資料。
- 不把小字連結藏在卡片底部要求精準點擊。
- 不用 AI 猜分類卻假裝是 GitHub 官方資料；用途標籤必須註明為規則推定。
- 不新增前端框架、後端、資料 schema 或執行期圖片依賴來完成單頁互動。
- 不複製參考圖的人物、地景、徽章、文案或具體構圖。
