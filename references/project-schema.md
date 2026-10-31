# 工程與時間軸

## project.json

`init_project.py` 會建立目錄與字體候選；填入當次影片的角色。下例是結構示意，不代表真實台詞或來源片段。

```json
{
  "status": "ready_to_render",
  "title": "本次對戰標題",
  "source_url": "https://www.youtube.com/watch?v=當次影片ID",
  "source_path": "source/video.mp4",
  "player_id": "lead",
  "characters": {
    "lead": {"name": "主角姓名", "sheet": "assets/lead.png", "role": "player", "reaction_poses": [6]},
    "rival": {"name": "對手姓名", "sheet": "assets/rival.png", "role": "opponent", "reaction_poses": [6, 7]}
  },
  "format": {"width": 1080, "height": 1920, "fps": 30, "logical_width": 270, "logical_height": 480},
  "timeline_path": "analysis/timeline.json",
  "audio_path": "audio/dialogue.wav",
  "sfx_path": "audio/sfx.wav",
  "registration_path": "assets/sprite_registration.json",
  "output": "output/pixel_battle_short.mp4",
  "font": "C:/Windows/Fonts/msjhbd.ttc",
  "font_regular": "C:/Windows/Fonts/msjh.ttc",
  "font_latin": "C:/Windows/Fonts/consola.ttf",
  "initial_hp": {"lead": 65, "rival": 100},
  "credit": "像素戲仿"
}
```

- 人物 ID 是任意穩定字串，畫面用 `name`。至少兩人，一人 `role: player`，其他人 `role: opponent`。每一回合只顯示主角和該事件的 `opponent`，因此第三位反應者可用切換 opponent 短暫出場。
- `reaction_poses` 使用實際生成的姿勢；普通表情 `[6]`，有兩格抓頭才設 `[6,7]`。
- 目前版型固定邏輯畫布 270×480，輸出需等比整數放大；預設放大四倍。字體需是真實存在且含繁體中文字形的檔案，可改為當地系統字體或工程內相對路徑。
- `status` 起初為 `needs_cast_and_edit`，資料與核對完成後才能改成 `ready_to_render`。空人物、缺素材、過期註冊、缺設定音效會拒絕。若不用 SFX，移除 `sfx_path` 而非留下不存在的檔案。
- 角色圖一旦重新生成，重跑註冊。註冊檔包含 SHA-256、八格 bounds、腳底 anchor、固定站立身高，不手改框來掩蓋生圖缺陷。

## edit_plan.json → timeline.json

下例用兩段各 3 秒、總長 6 秒說明映射。來源時間與文字都必須以本次實際片段替換。

```json
{
  "status": "source_reviewed",
  "total_duration": 6,
  "audio_edits": [
    {"id": "a", "type": "source", "source_start": 10, "source_end": 13},
    {"id": "b", "type": "source", "source_start": 20, "source_end": 23}
  ],
  "source_captions": [
    {"speaker": "lead", "source_start": 10.1, "source_end": 12.8, "text": "第一句實際原音"},
    {"speaker": "rival", "source_start": 20.1, "source_end": 22.8, "text": "第二句實際原音"}
  ],
  "chapters": [
    {"id": "round_one", "title": "本回合標題", "start": 0, "end": 6, "initial_hp": {"lead": 65, "rival": 100}}
  ],
  "events": [
    {
      "id": "drink", "start": 0, "end": 3, "chapter": "round_one",
      "speaker": "lead", "opponent": "rival", "target": "lead",
      "kind": "dialogue", "action": "drink", "effect": "water",
      "move": "補水", "attack_at": 0.3, "impact_at": 1, "heal": 24,
      "text": "第一句實際原音", "source_ranges": [{"start": 10, "end": 13}]
    },
    {
      "id": "counter", "start": 3, "end": 6, "chapter": "round_one",
      "speaker": "rival", "opponent": "rival", "target": "lead",
      "kind": "dialogue", "action": "attack", "effect": "electric",
      "move_before_impact": "準備反擊", "move": "本句關鍵招式",
      "attack_at": 0.3, "impact_at": 1.1, "damage": 26,
      "text": "第二句實際原音", "source_ranges": [{"start": 20, "end": 23}]
    }
  ]
}
```

### 音訊與字幕時間

- `audio_edits` 的陣列順序就是成片順序，不會按來源時間重新排序。每段可加自己的 `source_path`；省略時用工程來源。不能變速。
- 插入空白使用 `{"type":"silence","duration":0.3}`，並把其後所有事件／章節時間推遲 0.3 秒。`output_start/output_end` 可省略；若寫了，必須逐段連續、與原片段長度完全相符。
- `total_duration` 可省略，由音軌樣本數推算；若填，必須等於所有片段／靜音之和。編碼影格會按 `ceil(duration * fps)` 向上對齊，與音軌差小於一影格。
- `source_captions` 使用來源時間，assemble 產生 `captions` 的成片 start/end，保留來源、clip_id、partial 標記等。例：第二句的 20.1 秒映到成片 3.1 秒。
- 若同一段來源重播，字幕會映到每次出現；可用 `clip_id` 指定只套某次。使用 `--source-srt` 時優先於 plan 的字幕。
- 也可直接提供 `captions` 成片時間，但不要混淆兩種座標。帶 `source_start/source_end` 的 caption 仍會按來源映射。避免從舊 timeline 反覆 assemble 導致誤解；維持 edit_plan 為編輯來源。
- 字幕每則最多三行，長句在語意／停頓處拆分。字幕文字以原音為準，`events.text` 只供事件語意記錄；不能以不正確時間的全文蓋過實際 caption。
- SRT 嚴格使用各句 start/end。畫面的遊戲對話框則從 start 開始逐字顯示，讀完後保留到下一句或下一事件，模擬黃版文字框；這段保留不代表還在說話。若本片需要嚴格隱藏過期字幕，再將 renderer 的 caption 選擇限制在 start/end 內。

### 對戰事件

事件依成片時間遞增、不重疊，建議接續覆蓋整片。章節 ID／title 可任意，進新章節會重設 HP。章節開始／結束應與事件範圍一致。

| 欄位 | 用途 |
|---|---|
| `speaker` | 本事件動作角色 ID；對話事件是實際講者，reaction 可為無聲反應者 |
| `opponent` | 畫面右上角色，必須是非主角；若 speaker 非主角，兩者必須相同 |
| `target` | 受擊目標，需為主角或當下 opponent；喝水預設指定自己 |
| `kind` | `dialogue` 或 `reaction`；無聲抓頭不可生出新台詞 |
| `action` | 常用 `idle`、`drink`、`attack`、`react`；另可 `heal/counter/throw/cast/scratch/confusion/defeat/exasperated` |
| `effect` | `none/water/electric/moon/leaves/ball/fire`，新增特效要同時維護 renderer 與 SFX |
| `attack_at` | 相對事件開始的蓄力／抬杯時間，非全片絕對時間 |
| `impact_at` | 相對事件開始的命中／回復時間，需落在事件內且晚於蓄力 |
| `move` | 命中後招式名稱；`move_before_impact` 可避免提前洩漏笑點 |
| `damage/heal` | 0–100，預設攻擊 22、回復 24；目前 HP 最低保留 10，屬搞笑對戰效果 |
| `display_text` | reaction 文字，例如 `???`；不等於來源有這段台詞 |
| `response` | 命中後戰場提示，可留空；預設喝水／回復為「體力回復！」，其他有特效招式為「效果命中！」 |
| `source_ranges` | 來源回查用範圍，不會自動更改事件時間 |

喝水動作需至少留出約 1.6 秒，確保抬杯、接觸、放下都可見。命中後也要留出約 0.85 秒讓受擊／回彈完整。不要只為合規填時間數字；要與真正聽到的關鍵詞一致。
