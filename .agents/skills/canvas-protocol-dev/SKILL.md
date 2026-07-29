---
name: canvas-protocol-dev
description: 仅当 Infinite-Canvas 用户明确要求接入新的视频 / 图片生成协议、对接新中转站、修改既有协议字段映射或排查参考图未生效时使用。普通 bug 修复不得触发。
---

# Infinite-Canvas 生成协议接入

## 适用

接入新中转站或新协议家族、修改既有协议请求体、排查"能生成但参考图没生效"类问题。普通页面 bug、部署和审计不走本 Skill。

## 核心风险

协议字段猜错**不会报错**。上游对不认识的字段通常静默忽略，表现为纯文生视频正常出片、参考图 / 首尾帧 / 音频完全不起作用。因此任何协议接入都必须以**上游真实响应**为准，不得以既有协议同构为假设。

## 已实现协议

| 协议 | 画幅字段 | 参考素材字段 | 备注 |
|---|---|---|---|
| `chre3-video` | `size` | `image_refs` / `video_refs` / `audio_refs` | 普通，不带合规字段 |
| `chre3-video-real` | 同上 | 同上 | 真人合规，强制 `compliance_enabled=true` + `compliance_mode` |
| `cangyuan` | `aspect_ratio`（另有 `resolution`） | `reference_image_urls` / `reference_videos` / `reference_audios` | 苍元算力，仅 `seedance-flat` 家族；支持 `data:image` Base64 直传；成对 `first_frame`/`last_frame` 切首尾帧模式（与多模态互斥）；时长 4–15 秒 |

## 未实现（选中即静默降级为纯文生）

- `chat-video`：sora-2 / veo-3-1 系，参考图字段是 `images`。
- `omni-frame`
- `omni-v2v`

新增家族必须显式实现并补测试，不得靠既有分支兜底。

## 流程

1. **只读探测**：先用 `/v1/models` 等无费用端点确认站点真实返回结构。不得在此阶段触发付费生成。
2. **分类判定**：模型归类读 `supported_endpoint_types`，禁止按模型名称兜底。苍元站不返回 `type` / `category` / `capabilities`，名称兜底会把 6 个 seedance 模型误判成 chat。
3. **字段映射**：逐字段对照上游文档与真实响应，列出画幅、时长、分辨率、音频、参考图、首尾帧的映射表，随改动一并交付。
4. **实现**：在 `main.py` 内新增协议的请求体构造、提交轮询、验证探测、平台判定与路由；前端选项同步 `static/api-settings.html`、`static/js/api-settings.js`、`static/js/i18n/api-settings.js`，样式改动同步 `static/css/api-settings.css`。
5. **测试**：在 `tests/` 新增协议单测，参照 `test_cangyuan_protocol.py`、`test_chre3_protocol_split.py`。至少覆盖请求体构造、字段映射、路由判定和互斥模式。运行 `python -m pytest tests/ -v`，确认无回归。
6. **默认值审查**：任何影响产物的默认值必须在交付说明中点明。已知例：`audio` 映射画布"生成音频"开关（默认关），而上游默认开启，不勾选会得到无声视频。

## 授权边界

- 真实付费生成需要用户本轮明确授权，并先冻结模型、参数、次数上限。
- 无授权时只允许无费用端点探测和本地请求体断言。
- 中转站通常不返回账单金额或余额变化；只能确认任务成功，不能声称已核对扣费。这一限制必须写进残余风险。

## 交付

交付说明必须包含：字段映射表、已实现与未实现家族、测试结果、默认值变更影响、以及"哪些路径未经真实付费验证"。
