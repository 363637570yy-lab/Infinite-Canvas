# Canvas IR（中间稿）字段说明

中间稿是导演本与各目标提示词之间的唯一事实源。由后端 `video_prompt_targets.py` 规则抽取，不靠 AI。
转换 AI 只接收中间稿 JSON，不接收闲聊；台词、图号以中间稿为准，禁止改写。

## 字段

```json
{
  "duration_s": 15,
  "style": "柔光摄影，8K超高清",
  "shots": [
    {
      "index": 1,
      "at_s": null,
      "action": "皇帝坐在御花园石桌旁批阅奏折",
      "camera": "缓慢推进",
      "dialogue": [
        {"speaker": "甄嬛", "text": "臣妾参见皇上。"}
      ]
    }
  ],
  "subjects": [
    {"id": "皇帝", "image": "图1", "notes": "深金色龙袍"},
    {"id": "甄嬛", "image": "图2", "notes": ""}
  ],
  "images": [
    {"slot": "图1", "name": "阿川_ref.png", "index": 1, "matched": true},
    {"slot": "图2", "name": "小夏_ref.png", "index": 2, "matched": true}
  ],
  "sound": "",
  "warnings": ["词里引用了 @图3，但只挂载了 2 张图"]
}
```

## 抽取规则（逻辑层，非 AI）

- `@图N` / `图N` / `@文件名.png` 与 MEDIA 清单实名对齐；`index` 是上传顺序（从 1 起）。
  对不上的引用进 `warnings`，`matched: false`，不猜测、不发明。
- `台词：角色：「…」`、`角色：「…」` 抽成 `dialogue`，`text` 一字不改。
- `镜头N` / `[Shot N]` / `ACT` 标题切分 `shots`；没有时间码时 `at_s = null`。
- 风格尾巴（"柔光摄影 / 8K超高清"之类）收进 `style`。
- 用户写了环境声 / 配乐才填 `sound`，否则留空。
- 抽不出结构（纯一句话）时 `shots` 只有一条，多参 / 首尾帧目标要带「绑定不完整」警告。

## 时间码分配

`at_s` 为空时：转换阶段按 `duration_s` 均分估点，校验阶段夹紧到 `[0, duration_s]` 且严格递增。
第一镜永远不带时间码。
