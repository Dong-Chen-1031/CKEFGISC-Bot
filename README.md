# Discord.py Cogs Bot Template

> 一個基於 discord.py 的模組化 Discord 機器人模板，採用 Cogs 架構設計，支援自動熱重載和開發者工具。

> 這個 README 是 AI 寫的，但整個專案幾乎只有這個是 AI 寫的。

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://python.org)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-brightgreen.svg)](https://discordpy.readthedocs.io/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)


## ✨ 特色功能

- 🔧 **模組化設計** - 使用 Cogs 系統，功能模組獨立且易於維護
- 🔄 **自動熱重載** - 開發時無需重啟機器人即可重載模組，且可在檔案編輯時自動重新載入該 Cog
- 🛡️ **權限控制** - 開發者指令具有權限保護機制
- 📝 **完整日誌** - 使用 Rich 庫美化輸出，支援檔案和控制台雙重記錄
- ⚡ **斜線指令** - 支援現代化的 Discord 斜線指令
- 🎨 **美化界面** - 開發者指令統一的 Embed 樣式和互動式按鈕
- 🗒️ **訊息紀錄** - 訊息被刪除或編輯時自動送出通知，開發者可逐一伺服器開關

## 📁 專案結構

```
Discord-py-cogs/
├── bot.py              # 機器人主程式入口
├── settings.py         # 配置文件管理
├── requirements.txt    # 依賴套件清單
├── README.md          # 專案說明文件
├── .env               # 環境變數配置 (需要創建)
├── cogs/              # 功能模組目錄
│   ├── dev_cog.py       # 開發者工具模組
│   ├── countdown_cog.py # 日期倒數指令
│   ├── notify_cog.py    # 群發通知與已讀追蹤
│   ├── emoji_cog.py     # 跨伺服器複製表情符號
│   ├── msglog_cog.py    # 訊息刪除／編輯紀錄
│   └── example_cog.py   # 範例模組
├── utils/             # 工具函數目錄
│   ├── log.py         # 日誌系統
│   ├── ui.py          # UI 工具函數
│   ├── db.py          # 資料庫連線與初始化
│   ├── migrate.py     # 舊版 JSON 自動遷移
│   ├── timeparse.py   # 共用的時區與日期解析
│   ├── countdown.py   # 倒數資料表與查詢
│   ├── notify.py      # 群發資料表與查詢
│   ├── msglog.py      # 訊息紀錄設定資料表與查詢
│   └── types.py       # 型別定義
├── deploy/
│   └── CKEFGISC-Bot.service  # systemd 服務設定
├── data/              # 資料儲存 (自動產生)
│   └── bot.db         # SQLite 資料庫
└── logs/              # 日誌檔案目錄
    └── 2025-06-22.log # 每日日誌檔案
```

## 🚀 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 環境設定

在專案根目錄創建 `.env` 檔案：

```env
DISCORD_BOT_TOKEN=你的機器人Token

# 選填，不寫就是預設值
NOTIFY_REQUIRE_PERMISSION=true   # /notify 是否需要「管理伺服器」權限
NOTIFY_ALLOW_ANONYMOUS=true      # 是否允許匿名寄送
MESSAGE_LOG_ENABLED=true         # 訊息紀錄的總開關
```

這些開關都接受 `true/false`、`1/0`、`yes/no`、`on/off`，不分大小寫，改完要重啟機器人。

### 3. 配置開發者ID

編輯 `settings.py` 中的開發者ID：

```python
DEV_ID = [
    123456789012345678,  # 替換為你的 Discord 用戶 ID
    987654321098765432   # 可以添加多個開發者 ID
]
```

### 4. 運行機器人

```bash
python bot.py
```

### 5. 邀請機器人進伺服器

機器人登入後會在日誌印出邀請連結，也可以自己把 `<CLIENT_ID>` 換成 Developer Portal 裡的 Application ID：

```
https://discord.com/oauth2/authorize?client_id=<CLIENT_ID>&permissions=1345342608&scope=bot+applications.commands
```

連結裡的權限（`1345342608`）對應 `bot.py` 的 `INVITE_PERMISSIONS`：

| 權限 | 用途 |
|---|---|
| 檢視頻道、發送訊息、嵌入連結 | 訊息紀錄送通知 |
| 管理頻道 | 倒數頻道的建立、改名、刪除 |
| 管理身分組、連線、說話 | 倒數頻道鎖定（改 @everyone 的權限覆寫） |
| 管理表情符號 | 跨伺服器複製表情符號 |
| 查看稽核日誌 | 訊息紀錄顯示是誰刪的訊息（選用） |

另外 `bot.py` 使用 `Intents.all()`，記得在 Developer Portal 的 Bot 頁面打開 **Presence**、**Server Members**、**Message Content** 三個特權 Intent，否則機器人會登入失敗。

## ⏳ 日期倒數頻道

用語音頻道的名稱來顯示日期倒數，頻道預設會被鎖起來（大家看得到但不能加入），純粹當公佈欄用。
所有設定都用斜線指令完成，設定會存在 `data/countdowns.json`，重啟後照樣運作。

### 指令一覽

| 指令 | 用途 |
|------|------|
| `/countdown create` | 直接建立一個新的倒數語音頻道 |
| `/countdown bind` | 把倒數綁到已經存在的語音頻道上 |
| `/countdown edit` | 修改名稱、日期、時間或模板 |
| `/countdown list` | 列出這個伺服器所有倒數 |
| `/countdown remove` | 移除倒數（可選擇是否連頻道一起刪） |
| `/countdown refresh` | 立刻更新一次頻道名稱 |
| `/countdown help` | 查看名稱模板能用的變數 |

指令預設只有具備「管理頻道」權限的人看得到，可以在伺服器設定裡調整。

### 快速上手

```
/countdown create name:畢業典禮 date:2026-06-15
```

頻道就會變成 `畢業典禮 剩 322 天`，並自動鎖住不讓人加入。

想用現成的頻道就改用 `bind`（注意原本的頻道名稱會被覆蓋）：

```
/countdown bind channel:#倒數 name:期末考 date:1/20 time:08:30
```

### 名稱模板

`template` 參數可以自訂頻道要長什麼樣子，預設是 `{name} 剩 {days} 天`：

| 變數 | 說明 |
|------|------|
| `{name}` | 倒數的名稱 |
| `{days}` | 剩餘天數（以日曆日計算，今天到期為 0） |
| `{d}` `{h}` `{m}` | 精確的剩餘天 / 時 / 分 |
| `{total_days}` `{total_hours}` `{total_minutes}` | 剩餘總天數 / 總小時 / 總分鐘 |
| `{target}` `{target_date}` `{target_time}` | 目標時間 / 日期 / 時刻 |

範例：

```
/countdown create name:成發 date:2026-03-08 template:📅 {name} 還有 {days} 天
/countdown edit channel:#倒數 template:{name} | {d}天{h}時
```

倒數結束後會改用 `expired_template`（預設 `{name} 時間到`），可以用 `/countdown edit` 改。

### 日期格式

`date` 接受 `2026-06-15`、`2026/6/15`、`6/15`（視為今年）；
`time` 接受 `08:30`、`8:30`、`8`，不給的話是當天 00:00。

### ⚠️ 關於更新頻率

Discord 限制**每個頻道 10 分鐘只能改名 2 次**，所以機器人每 10 分鐘才檢查一次，
而且只在名稱真的變了才送出請求。以天為單位的倒數實際上一天只會改名一次，不會有問題。

時區、儲存路徑、更新間隔都在 `settings.py` 調整：

```python
TIMEZONE = "Asia/Taipei"
COUNTDOWN_DATA_FILE = "data/countdowns.json"
COUNTDOWN_UPDATE_INTERVAL = 10  # 分鐘，不建議低於 5
```

### 機器人需要的權限

- **管理頻道** — 建立頻道、改名、刪除
- **管理身分組**、**連線**、**說話** — 鎖定頻道時要改 @everyone 的權限覆寫
- 建議把機器人的身分組放在夠高的位置，否則改不動頻道

## 📨 群發通知（已讀追蹤）

把通知私訊給整個身分組，每則訊息下面附一顆「✅ 已讀」按鈕，
成員按下去就記錄起來，之後可以用指令查誰讀了、誰還沒。

### 指令一覽

| 指令 | 用途 |
|------|------|
| `/notify send` | 群發私訊（身分組、個別成員、排程都用這個） |
| `/notify autoremind` | 事後調整或關閉某則通知的自動提醒 |
| `/notify status` | 查看某則通知誰已讀、誰未讀 |
| `/notify list` | 列出最近 10 筆群發及已讀率 |
| `/notify remind` | 再私訊提醒一次還沒按已讀的人 |
| `/notify delete` | 刪除群發紀錄 |

### 誰能使用

預設只有具備「管理伺服器」權限的人看得到、用得到。想開放給所有人，在 `.env` 裡加一行：

```env
NOTIFY_REQUIRE_PERMISSION=false
```

接受 `true/false`、`1/0`、`yes/no`、`on/off`，不分大小寫；沒設就是 `true`。
**改完要重啟機器人**才會生效（開機時 `bot.tree.sync()` 會把新的權限設定同步給 Discord）。

權限是兩層一起切的：

| | `true`（預設） | `false` |
|---|---|---|
| Discord 端 `default_permissions` | 需要管理伺服器 | 不限制 |
| Bot 端 `interaction_check` | 再驗一次 | 直接放行 |

之所以要兩層，是因為 `default_permissions` 只是**預設值** —— 伺服器管理員可以在
「設定 → 整合 → 機器人」裡覆寫它。多一層 bot 端檢查，即使 Discord 端被改開也還是擋得住。

> ⚠️ 關掉之後**任何成員都能用 `/notify send` 一次私訊整個身分組**。
> 人多的伺服器請三思，這種東西被亂用很煩。

> 另外注意：如果管理員先前已經在「整合」裡手動覆寫過 `/notify` 的權限，
> 那個覆寫會一直存在，改 `.env` 不會把它清掉，要自己回去那邊移除。

### 怎麼發

```
/notify send role:@全體成員 channel:#會議室
```

送出後會跳出一個視窗填**標題**和**內容**（內容可以換行），按確認就開始寄。
`channel` 是選填的，填了會在通知裡附上該頻道的連結。

### 三種指定對象的方式

全部集中在 `/notify send`，所有參數都是選填的：

| 你想做的事 | 指令 |
|-----------|------|
| 發給整個身分組 | `/notify send role:@全體成員` |
| 只發給一個人 | `/notify send user:@某人` |
| 發給挑選的多個人 | `/notify send`（**都不填**，會出現成員選單） |
| 身分組 **+** 額外幾個人 | `/notify send role:@幹部 user:@顧問` |

都不指定對象時會先出現一個成員選單（可搜尋、可多選，上限 25 人 —— Discord 選單的硬上限），
選完才跳出填標題內容的視窗。

`role` 和 `user` 可以並用，**重複的人只會收到一封** —— 已經在身分組裡的人再被單獨指定也不會重複寄。
機器人一律自動排除。

不管用哪種方式，產生的通知**長得完全一樣**，已讀追蹤、`status`、`remind` 全部通用。

### 隱藏寄送者

加上 `anonymous:True` 收件者就看不到是誰發的：

```
/notify send role:@全體成員 anonymous:True
```

footer 會從「📌 看到訊息請按下方的「已讀」按鈕｜發布者:王小明｜我的伺服器」
變成「📌 看到訊息請按下方的「已讀」按鈕｜我的伺服器」，圖示也從發布者頭像換成伺服器圖示
（不換的話等於直接指出是誰發的）。

> 這只對**收件者**隱藏。`/notify status` 仍然會顯示發布者是誰並標記「🕵️ 匿名寄送」，
> 資料庫的 `author_id` 也原封不動存著，**任何能登入伺服器的人都查得出來是誰發的**。
> 所以這是「不讓收件者看到」，不是真正的匿名管道。

想整個停用這個功能，在 `.env` 裡設：

```env
NOTIFY_ALLOW_ANONYMOUS=false
```

關掉之後有人用 `anonymous:True` 會被擋下並看到提示，**不會默默改成具名寄送** ——
否則發的人會以為自己匿名了，那比不能用還糟。沒設就是 `true`（可用）。

> **已經發出去的舊通知不受影響。** 如果一則通知當初是匿名發的，之後就算把開關關掉，
> 它的自動提醒重寄時仍然維持匿名。這是刻意的 —— 事後把當初承諾匿名的發布者揭露出來
> 不太妥當，而且已經送出的私訊本來也改不了。開關只管新發出的通知。

### 排程寄送

加上 `schedule` 就不會馬上寄，而是到時間才自動送出：

```
/notify send role:@全體成員 schedule:2026-08-01 09:00
```

日期格式跟倒數一樣（`2026-08-01`、`8/1`、可加 `09:00`），只接受未來的時間。
機器人每分鐘檢查一次，到點自動寄出並記錄結果。

**收件者是到寄送當下才決定的** —— 排程期間身分組有人加入或退出，都會反映到實際寄送名單。
`/notify list` 會把還沒寄的標成 ⏰，`/notify status` 則顯示預定時間與內容預覽。
還沒寄出前用 `/notify delete id:N` 就等於取消排程。

### 自動提醒沒看的人

`remind_every` 設定每隔幾小時自動私訊還沒按已讀的人：

```
/notify send role:@全體成員 remind_every:6 remind_max:3
```

上面是每 6 小時提醒一次、最多 3 次。時間從**實際寄出**開始算（排程的通知會等寄出後才起算），
每次提醒後重新計時。全部人都按了已讀就自動停止，次數用完也會停。

**上次私訊失敗的人也會一起重試**，對方之後開放私訊就補得到。

事後想調整或關掉：

```
/notify autoremind id:3 every_hours:12 max_times:2
/notify autoremind id:3 every_hours:0
```

`every_hours:0` 就是關閉。重新設定會把已提醒次數歸零重新計算。
`/notify status` 會顯示已提醒幾次和下次提醒時間。

每位成員收到的私訊長這樣：

```
【公告】第 5 次幹部會議通知
敬請全體成員於 12/14（日）11:00 前，準時進入會議室頻道參加會議。
#會議室
─────────────────────────────
🖼 📌 看到訊息請按下方的「已讀」按鈕
   發布者:王小明 | 我的伺服器
          [ ✅ 已讀 ]
```

提示文字和發布者放在 embed 的 footer，左邊圖示是發布者的頭像。
按下按鈕後訊息會直接變成綠色，footer 改成「✅ 你已於 ⋯ 確認已讀」，按鈕同時鎖起來。

> ⚠️ Discord 的 embed **footer 不會渲染提及**，所以發布者顯示的是名稱而不是 `@提及`，
> 這是平台限制不是 bug。想要可點擊的 `@提及` 就得放回內文。

### 查已讀狀況

```
/notify status id:3
```

```
📊 已讀狀況 — #3
【公告】第 5 次幹部會議通知
████████░░ 16/20 (80%)

✅ 已讀 (16)   @A @B @C …
⬜ 未讀 (4)    @D @E @F @G
⚠️ 私訊失敗 (1) @H
```

「私訊失敗」是關閉私訊或封鎖了機器人的人，他們根本沒收到，所以跟「未讀」分開算，
已讀率的分母也不會把他們算進去。

`/notify remind id:3` 會重新私訊未讀的人，**連上次沒送出去的也會一起重試**，
如果對方已經開放私訊就會成功補發。

### ⚠️ 注意事項

- 私訊每封之間會間隔 1 秒（`settings.NOTIFY_SEND_DELAY`），避免被 Discord 限流。
  20 人大約 20 秒，指令會先回「開始群發」，寄完再回報結果。
- 機器人只能私訊**跟它有共同伺服器**、且沒把私訊關掉的人。
- 需要在 Discord 開發者後台開啟 **Server Members Intent**，否則抓不到身分組成員。
- 已讀紀錄存在資料庫，機器人重啟後按鈕依然有效
  （用 `discord.ui.DynamicItem` 實作持久化按鈕）。

## 🗒️ 訊息紀錄

訊息被刪除或編輯時，往指定的頻道送一則整理好的通知。
只有 `settings.DEV_ID` 裡的開發者能設定，而且 `.env` 的 `MESSAGE_LOG_ENABLED` 要是開的。

### 指令一覽

| 指令 | 用途 |
|------|------|
| `/msglog set` | 指定要接收紀錄的頻道（同時啟用） |
| `/msglog config` | 調整細項開關；全部留空就是查看目前設定 |
| `/msglog ignore` | 把頻道或使用者加進／移出忽略清單 |
| `/msglog enable`／`disable` | 暫停或恢復某個伺服器（設定會保留） |
| `/msglog status` | 查看單一伺服器的設定與權限檢查 |
| `/msglog list` | 列出所有設定過的伺服器 |
| `/msglog test` | 送一則範例通知，確認權限與外觀 |
| `/msglog remove` | 刪除某個伺服器的設定 |

### 誰能使用

兩層檢查，兩層都要通過：

1. **總開關** —— `.env` 裡的 `MESSAGE_LOG_ENABLED`。設成 `false` 之後 `/msglog` 指令會直接
   拒絕使用，所有伺服器也一律停止記錄，但資料庫裡的設定原封不動留著，之後再打開就恢復。
2. **開發者** —— 使用者 ID 要在 `settings.DEV_ID` 裡。
   指令另外掛了「管理員」的預設權限，單純是讓一般成員在指令選單裡看不到它，
   真正把關的是 `DEV_ID`，伺服器管理員自己調權限也繞不過去。

### 快速上手

在要記錄的伺服器裡：

```
/msglog set channel:#訊息紀錄
/msglog test
```

`channel` 有自動完成，只會列出**機器人能發言、也能貼嵌入**的文字頻道。

### 指定其他伺服器

每個指令都有 `guild` 參數（一樣有自動完成，已經設定過的伺服器前面會有 ✅），
所以不必人在那個伺服器裡，在私訊裡也能設定：

```
/msglog set guild:某某伺服器 channel:#訊息紀錄
/msglog disable guild:某某伺服器
```

先選 `guild`，`channel` 的自動完成就會跟著換成那個伺服器的頻道。
留空的話就是你目前所在的伺服器（在私訊裡使用時一定要指定）。

### 可以調整什麼

`/msglog config` 的每個選項都可以單獨開關，沒填到的維持原樣：

| 選項 | 預設 | 說明 |
|------|------|------|
| `log_delete` | 開 | 訊息被刪除時發送通知 |
| `log_edit` | 開 | 訊息被編輯時發送通知 |
| `log_bulk_delete` | 開 | 訊息被整批清除時發送彙整通知 |
| `ignore_bots` | 開 | 不記錄機器人與 Webhook 的訊息 |
| `include_content` | 開 | 關掉之後只留作者、頻道等資訊，不留訊息文字 |
| `log_attachments` | 開 | 是否列出附件、貼圖、投票等非文字內容 |
| `log_embed_only_edits` | 關 | Discord 補上連結預覽時也算一次編輯，預設不記錄 |
| `check_audit_log` | 開 | 嘗試從稽核紀錄找出是誰刪的 |

忽略清單則用 `/msglog ignore`，頻道和使用者共用同一個指令：

```
/msglog ignore action:新增 target:#閒聊
/msglog ignore action:新增 target:@某個機器人
/msglog ignore action:清空全部
```

`target` 的自動完成會同時列出頻道和成員，也可以直接貼 ID。
忽略一個頻道時，**底下的討論串也會一起被忽略**。

### 支援的訊息類型

通知會盡量把訊息的每個部分都攤開來，不是只有純文字：

| 類型 | 通知裡會看到 |
|------|--------------|
| 文字 | 內容全文，太長會截斷（`settings.MESSAGE_LOG_CONTENT_LIMIT`） |
| 附件 | 檔名、大小、原始連結，圖片還會直接附上預覽 |
| 語音訊息 | 標成 🎙️ 語音訊息 |
| 劇透附件 | 檔名後面標「（劇透）」，而且不會自動展開預覽 |
| 貼圖 | 貼圖名稱與圖片連結 |
| 投票 | 題目、每個選項的得票數、是否可複選 |
| 回覆 | 一顆連到原訊息的連結 |
| 轉發訊息 | 被轉發的原文內容摘要 |
| 機器人 / Webhook 嵌入 | 各嵌入的類型與標題 |
| 按鈕、選單 | 有幾列元件 |
| 系統訊息 | 中文的訊息類型（釘選訊息、建立討論串…） |
| 討論串裡的訊息 | 標成 🧵 並一併顯示父頻道 |

編輯通知會並排「修改前 / 修改後」，右下角附一顆跳到該訊息的按鈕。
批次刪除（管理員清訊息、機器人 purge）則彙整成一則，列出前 10 筆內容預覽。

### ⚠️ 注意事項

- **快取限制**：機器人只認得自己啟動之後看過的訊息。更早以前的訊息被刪掉時，
  通知只會有訊息 ID 和頻道，內容拿不到 —— 這是 Discord 的限制，不是壞掉。
  編輯就沒這個問題，Discord 會把編輯後的完整內容一起送過來。
- **附件連結**：訊息一旦被刪除，Discord 的 CDN 連結很快就會失效。
  通知是即時送出的，當下通常還看得到，但過一陣子再回頭點就不一定了。
- **刪除者只是推測**：自己刪自己的訊息不會留下稽核紀錄，而且 Discord 會把短時間內的
  多次刪除合併成一筆，所以「刪除者」欄位只有在稽核紀錄對得上時才會出現。
  這個功能還需要機器人有「查看稽核日誌」權限。
- **連結預覽**：Discord 幫訊息補上連結預覽時也算一次編輯事件。
  預設會過濾掉（判斷依據是訊息沒有編輯時間），想看的話開 `log_embed_only_edits`。
- 紀錄頻道本身永遠不會被記錄，免得無限循環。
- 紀錄頻道被刪掉的話，該伺服器的訊息紀錄會自動停用，log 裡會留一行警告。
- 私訊不會被記錄。

## 🗄️ 資料儲存

資料存在 SQLite（`data/bot.db`），透過 SQLModel / SQLAlchemy 操作。

| 資料表 | 內容 |
|--------|------|
| `countdown` | 倒數設定，以 `channel_id` 為主鍵 |
| `broadcast` | 每則群發通知的內容與設定 |
| `broadcast_target` | 收件者，一人一列，記錄已讀時間與私訊是否失敗 |
| `message_log_config` | 各伺服器的訊息紀錄設定，以 `guild_id` 為主鍵 |
| `meta` | 雜項，目前只記錄 JSON 是否遷移過 |

收件者拆成獨立的資料表，所以「誰已讀、誰沒讀、誰收不到」都是可以直接查詢的欄位，
而不是塞在一坨 JSON 裡。刪除通知時收件者會級聯一起刪掉。

### 從舊版 JSON 自動遷移

開機時如果資料庫是空的、而舊的 `data/countdowns.json` 或 `data/notifications.json` 還在，
就會自動把資料搬進資料庫，log 會顯示搬了幾筆：

```
INFO  偵測到舊版 JSON 資料，開始遷移到資料庫…
INFO  遷移完成：倒數 1 筆、群發 12 筆。舊的 JSON 檔已保留，確認無誤後可自行刪除。
```

搬完會在 `meta` 表記一筆，**之後不會再搬第二次** —— 否則你手動刪掉舊通知後，
重開機又會被舊 JSON 灌回來。舊檔案不會被刪除或改名，確認資料無誤後自己刪就好。

舊資料沒有的欄位會給合理的預設值：`sent_at` 補成建立時間（舊的都是立即寄送）、
排程與自動提醒則是關閉。

## 🎨 跨伺服器複製表情符號

把另一個伺服器的表符整批搬到目前的伺服器。**在目標伺服器執行**，指定來源。

| 指令 | 用途 |
|------|------|
| `/emoji copy` | 從指定伺服器複製表符到這裡 |
| `/emoji list` | 看看有哪些伺服器可以當來源 |

```
/emoji copy source:<從選單挑>
```

`source` 有自動完成，會列出**機器人和你都在、而且你有管理表情符號權限**的伺服器，
顯示成「伺服器名稱 (37 個表符)」，不用去找伺服器 ID。

### 流程

送出後不會馬上動手，而是先給你一份預覽：

```
🎨 表情符號複製
社團舊伺服器 → 社團新伺服器

✅ 將複製 24 個   ⏭️ 同名跳過 3 個   🚫 額度不足 0 個
目標伺服器目前用量：靜態 12/50・動態 4/50
清單：`kekw`、`pepehands`、`catjam` …等 24 個

          [ ✅ 開始複製 ]  [ ✖️ 取消 ]
```

按下確認才會實際建立，完成後回報成功／跳過／失敗的數量與新表符。

### 幾個設計上的重點

**權限要兩邊都有。** 你必須在**來源伺服器**也有「管理表情符號」權限 ——
否則只要機器人在某個伺服器裡，任何人都能把那裡的表符整包搬走。
目標伺服器則是靠 `default_permissions` 加 bot 端 `interaction_check` 雙重檢查。

**額度是靜態與動態分開算的。** Discord 給的 `emoji_limit` 是**各自**的上限
（未加成 50/50，加成後最多 250/250），所以動態表符額滿不影響靜態還能不能複製。
額度不足的會明確列出來，不會默默失敗。

**同名預設跳過**，可以用 `skip_existing:False` 改成照樣複製（Discord 允許同名表符）。

**每個之間間隔 1.5 秒**（`settings.EMOJI_COPY_DELAY`）。建立表符的速率限制比發訊息緊得多，
調太低整批作業會被 429 卡住。50 個大約要 75 秒。

**機器人需要「管理表情符號」權限**（`manage_expressions`），而且要在來源和目標兩個伺服器裡。

## 🔧 開發者工具

### 指令前綴
- 開發者指令前綴：`.dev `
- 不使用斜線指令是為了避免出現於使用者選單干擾使用者體驗

### 🎮 可用指令

#### 📦 模組管理指令

##### `.dev load <模組名稱/all>`
載入指定的模組或所有模組
```
.dev load example_cog    # 載入 example_cog 模組
.dev load all           # 載入所有模組
```

##### `.dev unload <模組名稱/all>`
卸載指定的模組或所有模組
```
.dev unload example_cog  # 卸載 example_cog 模組
.dev unload all         # 卸載所有模組
```

##### `.dev reload [模組名稱/all]`
重新載入指定的模組或所有模組
```
.dev reload example_cog  # 重新載入 example_cog 模組
.dev reload all          # 重新載入所有模組
.dev reload              # 顯示互動式按鈕選擇模組
```

#### 🔄 系統管理指令

##### `.dev restart`
重啟機器人
```
.dev restart            # 完全重啟機器人程序
```

##### `.dev test`
可在程式碼中放置簡單的邏輯，測試機器人是否正常運作
```
.dev test               # 回應「測試成功！」確認機器人狀態
```

### 🎯 互動式模組管理
當使用 `.dev reload` 不帶參數時，機器人會顯示互動式按鈕界面：

![互動式模組管理界面](https://github.com/Dong-Chen-1031/Discord.py-Cogs-Bot-Template/blob/main/screenshots/reload.png?raw=true)

- **All 按鈕** - 重載所有模組
- **個別模組按鈕** - 重載特定模組
- **即時反饋** - 顯示載入結果和錯誤訊息
- **自動更新** - 按鈕點擊後自動刷新界面

### 🔒 權限保護
- 所有開發者指令都有權限檢查
- 只有在 `settings.py` 中定義的 `DEV_ID` 可以使用
- 非授權用戶嘗試使用會被拒絕

### ⚡ 自動重載功能
如果在 `settings.py` 中設定 `AUTO_RELOAD = True`：
- 自動監控 `cogs/` 目錄下的檔案變更
- 檔案儲存時自動重載對應模組
- 適合開發時使用，生產環境建議關閉

## 📝 創建新模組

### 基本模組範本

在 `cogs/` 目錄下創建新的 `.py` 檔案：

```python
import logging
from discord.ext import commands
from discord import app_commands, Interaction
from utils.log import log

class YourCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="example", description="範例指令")
    async def example_command(self, interaction: Interaction):
        await interaction.response.send_message("Hello World!")

async def setup(bot: commands.Bot):
    await bot.add_cog(YourCog(bot))
    logging.info(f'{__name__} 已載入')
```

### 模組自動載入

所有在 `cogs/` 目錄下的 `.py` 檔案會在機器人啟動時自動載入。

## 🛠️ 核心組件

### Bot 主程式 (`bot.py`)
- 機器人初始化
- 自動載入所有模組
- 斜線指令同步
- 異步啟動流程

### 設定管理 (`settings.py`)
- 環境變數載入
- 開發者權限配置
- 機器人基本設定

### 日誌系統 (`utils/log.py`)
- Rich 美化輸出
- 檔案和控制台雙重記錄
- 自動按日期分類日誌
- 快速日誌函數

### UI 工具 (`utils/ui.py`)
- 統一的 Embed 樣式
- 開發者專用樣式
- 可自訂顏色和內容

## 📚 使用範例

### 基本指令回應
```python
@app_commands.command(name="ping", description="檢查機器人延遲")
async def ping(self, interaction: Interaction):
    latency = round(self.bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! 延遲: {latency}ms")
```

### 使用日誌系統
```python
from utils.log import log

# 記錄用戶互動
log(interaction, command_used="ping")

# 記錄一般資訊
log("機器人已啟動")
```

### 使用 UI 工具
```python
from utils.ui import info_embed, dev_embed

# 建立資訊嵌入
embed = info_embed("操作成功完成！")
await interaction.response.send_message(embed=embed)
```

## 🔒 權限系統

機器人內建開發者權限系統：

- 在 `settings.py` 中設定開發者 Discord ID
- 開發者可以使用模組重載等管理功能
- 非開發者無法執行敏感操作

## 📋 依賴套件

- `discord.py` - Discord API 包裝器
- `python-dotenv` - 環境變數管理
- `rich` - 美化控制台輸出
- `watchdog` - 檔案監控 (用於自動重載)

## 📄 授權條款

此專案採用 MIT 授權條款 - 詳見 [LICENSE](LICENSE) 檔案

## 🆘 常見問題

### Q: 機器人無法啟動？
A: 請檢查：
- `.env` 檔案是否正確設定
- Discord Bot Token 是否有效

### Q: 模組重載失敗？
A: 請檢查：
- 模組語法是否正確
- 是否有權限執行開發者指令
- 查看日誌檔案了解詳細錯誤

### Q: 斜線指令不出現？
A: 請確認：
- 機器人有足夠的權限
- 指令已正確同步
- 等待 Discord 更新 (可能需要幾分鐘)

## 📞 聯絡資訊

如有問題或建議，歡迎開啟 Issue 或聯絡專案維護者。

---

*最後更新：2025年12月5日*
