# 人物動作與招式

讀 `assets/style-reference.png` 看本流程的完成品質：奶白場景、左下大背側人物、右上全身對手、清楚 HP 與對話框。該圖的人物只是舊案，不是新片預設角色。

## 人物資料

先從當次來源取清楚的臉、髮型、眼鏡、衣服參考。保留形象辨識特徵；不要把所有人都套成西裝，也不要讓角色身份隨姿勢改變。人物名稱不確定時先保留來源稱謂或中性角色名。

玩家從背側朝右，仍看得見側臉；對手從正側朝左。通常兩人足夠，第三人有重要反應才加，不要強制湊三人。

## 原生 ImageGen 提示詞骨架

呼叫前看過本機參考圖。把每個人物視為一份獨立素材，可平行生成。`transparent_background=true`，輸出後複製到專案。不要由腳本自行改走其他生圖 API。

先生成單一角色或已有穩定角色圖時，將它作 identity reference，再產八格 sheet：

```text
Use case: identity-preserve.
Create a production animation sprite sheet of the SAME character shown in the reference.
Subject: {{name, recognizable face/hair/wardrobe from this video}}.
Camera: {{back three-quarter facing right for player; front three-quarter facing left for opponent}} in EVERY frame.
STRICT 4 columns × 2 rows, eight equally spaced cells, landscape 3:2 canvas.
One complete full-body character in each cell. Same identity, clothes, body proportions,
pixel size, scale and foot baseline. Keep hands and both shoes safely inside each cell,
with padding. Coarse Game Boy Color / Pokémon Yellow battle-sprite pixel clusters,
stepped dark outlines, limited flat colors, no gradients, no antialiasing.
Read left-to-right on row one, then row two:
0 idle, empty relaxed hands;
1 cup raised toward mouth, elbow bent;
2 cup touching lips, head slightly tilted back, clearly drinking;
3 attack wind-up, elbow pulled back, torso twisted;
4 attack release, arm extended toward the opponent, forward lean;
5 hit recoil, head and torso noticeably backward, eyes squeezed, defensive bent arms;
6 expressive talking/taunting/reaction pose;
7 defensive block, OR second distinct reaction pose when the scene needs one.
For head scratching use pose 6 with fingers touching the crown and pose 7 with fingers
at the back of the head, different elbow and head angles, so they animate together.
Articulate arms, head and torso: do not repeat translated copies of one pose.
Genuine transparent background, no labels, cell borders, text, ground or cast shadows.
```

飲水不是每個影片都需要；沒有飲水段可把 1/2 換成該片最重要的準備／接觸動作，並同步修改動作調度。不要假設新片一定要水杯、月亮或曲棍球。

## 切格與配準

ImageGen 的手臂可能跨越名義格線。`register_sprites.py` 用 alpha 的大連通塊辨識八個人物，保存 `bounds`、`anchor_sheet` 與 `standing_height`，不修改原 PNG。

- 輸出需有八個可用姿態；不完整、黏在一起或格序錯誤時，先看圖修正生圖或明確的 frame metadata。
- 共用站立高度決定縮放，各姿勢腳底錨點對齊。不能每格各自「塞滿框」，否則伸手時人物會縮小。
- 不可因固定 4×2 裁切而截掉伸出的手或把鄰格人物帶入。
- 使用 nearest-neighbor 放大。保留原 PNG，所有合成／裁切只發生在影片圖層。
- Alpha 最大值 253/254 不必然是錯誤；檢查背景透明比例與實際白底合成結果。背景 RGB 顏色不能代替 alpha 判斷。

## 動作節奏

- 喝水：0→1→2→1→0；杯子在 2 必須碰到嘴，不是飄在臉前。接觸姿態約保留 0.5–0.9 秒。
- 出招：0→3→4→0，3 對應蓄力，4 對應釋放，通常保留約 0.4–0.7 秒。
- 受擊：5→7/0→5/0，再回復；可配短震動與一次閃爍，不能只讓靜圖消失。
- 抓頭：6↔7 明顯交替，顯示問號但不加假配音。
- 長台詞：低頻在 idle/reaction 間切換，保留節奏；不讓人物持續無原因抽動。

所有秒數是起點，按原音動作與句長調整。命中點依關鍵詞，不把每句固定在開頭一秒打出去。

## 招式選擇

程序特效屬遊戲圖形，可由 Pillow/canvas 繪製，不需要 ImageGen 重畫 UI。

| effect | 適合表現 | 重點 |
|---|---|---|
| water | 喝水／回復／水系 | 回復時水滴向上，不對自己扣血 |
| electric | 反擊／尖銳句子 | 黑邊黃心粗電弧、短爆點、局部暗場 |
| moon | 月亮／比喻 | 依台詞畫滿月或月相，不遮 HP；光束與星芒 |
| leaves | 蔬菜／菜蟲 | 葉片與蟲群由出招者飛向目標 |
| ball | 球／投擲／反問 | 拋物線、命中、反彈，別只有胸口一顆靜球 |
| fire | 放火／猛烈收尾 | 移動火舌與命中火焰，字幕區保持乾淨 |
| none | 無聲反應／轉場 | 不硬加傷害 |

無相符效果時新增一種，不把任意話題套成菜蟲或月亮。HP、屬性、招式名是戲仿介面，不是人物真的說出的話，也不必做出整片總勝負。

畫面可作一次短暗色／單次閃爍、速度線、後仰、血條下降；避免高速全螢幕白閃。原音期間壓低配樂，短命中音仍需聽得見。前案約 -20 dBFS 的命中音可作起點，最終以混音後對話可辨與無削波為準。
