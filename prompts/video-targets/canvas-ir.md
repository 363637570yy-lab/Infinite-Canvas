# Canvas IR（中间稿）字段说明

中间稿是导演本与各目标提示词之间的结构事实源。由后端 `video_prompt_targets.py` 规则抽取，不靠 AI。
转换 AI 同时接收：原始导演本、按槽位附图、图槽清单（图号 + 文件名）、中间稿 JSON、目标生成规则。
台词、图号以中间稿和槽位清单为准；画面外观以附图为准。中间稿的 `images[]` 不含 url，地址只作为附图发送。

## 字段

```json
{
  "source_prompt": "皇帝@图1 坐在石桌旁……",
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
    {"slot": "图1", "name": "阿川_ref.png", "index": 1, "role": "", "referenced": true},
    {"slot": "图2", "name": "小夏_ref.png", "index": 2, "role": "", "referenced": true}
  ],
  "sound": "",
  "warnings": ["词里引用了 @图3，但只挂载了 2 张图"]
}
```

## 抽取规则（逻辑层，非 AI）

- `@图N` / `图N` / `@文件名.png` 与 MEDIA 清单实名对齐；`index` 是上传顺序（从 1 起）。
  对不上的引用进 `warnings`，`referenced: false`，不猜测、不发明。
- `台词：角色：「…」`、`角色：「…」` 抽成 `dialogue`，`text` 一字不改。
- `镜头N` / `[Shot N]` / `ACT` 标题切分 `shots`。
- 风格尾巴（"柔光摄影 / 8K超高清"之类）收进 `style`。
- 用户写了环境声 / 配乐才填 `sound`，否则留空。
- 抽不出结构（纯一句话）时 `shots` 只有一条，并带「绑定不完整」警告。subjects 可以为空；转换时由模型看图补环境/布局 Subject。

## 时间码

词里写了 `3s` / `3秒` 的，写入 `at_s`。
没写的：第一镜 `at_s = null`（对应 [Shot 1] 不带时间码）；其余镜按时长均分估点。
校验只要求输出时间码递增且小于 `duration_s`，允许模型在估点附近微调。
