# 本地任务、收文与证据账本

作者：Wangyuan · 2026-10-29

本项目源码采用 MIT 许可证，见 LICENSE；第三方依赖仍适用各自上游许可。

## 可用能力

- SQLite原子事务、来源去重、同幂等键重试和内容冲突拒绝；按月编号。
- 收文/任务/案件/交接/会议共用明确生命周期；乐观版本，修订及追加证据使旧复核失效；完成必须有证据和人工确认。
- 通用中文义务、固定日期、相对期限、工作日及跨段上下文候选；显式工作日日历，不自动指定密级和紧急等级。
- 原文锚点校验、人工确认后的任务批量原子导入、恢复重试；附件内容指纹；文本及受限DOCX提取。
- 显式时区逾期统计、月度筛选、疑似重名、审计记录、材料覆盖/有效期、原因代码与原文引用验证；只生成本地消息草稿。

## 运行

Python 3.11+。依赖仅为标准库，或 requirements.txt 明确列出的开源解析库。程序默认不联网；安装依赖时由包管理器下载。

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -B cli.py demo
python3 -B cli.py demo --output demo-output
python3 -B cli.py run example.json --output demo-output
python3 -B -m unittest discover -s tests -v
```

演示数据从零生成；默认临时目录会在命令结束时释放。需要保留文件或数据库时必须指定 --output，输入与输出目录应由使用者自行控制。JSON输出到标准输出，参数错误返回非零状态。

## 输入与输出

动作：`create`、`change`、`get`、`report`、`draft`、`import_candidates`、`candidates`、`coverage`、`confirmed_reason`、`extract_text`。每个动作参数名称对应核心文件中的同名函数/方法签名，公共接口有类型标注及中文说明。通用调用格式是 action 与 args；目录、收集模块另接受 tools/events/contract 等顶层显式合同。

```json
{
  "action": "candidates",
  "args": {
    "text": "请资料部于2030年1月3日前反馈核对结果。",
    "received_on": "2030-01-01"
  }
}
```

数据库位置由 --output 指定；未指定时写临时目录，结束即释放。change 须传 id 对应的 identifier、expected_version、action、actor、operation_key、values。纯日期截止按报告 now 所带时区的日期判断，截止当日仍有效；具体时刻用 due_at。中文结果均为待确认候选，解析覆盖不是法律或制度认定。附件仅保存字节，不自动执行。

## 保留边界

原业务数据连接、组织角色权限、真实模板/名单、消息发送、外部OCR/语音/模型、浏览器审批和生产集成均未包含；批量导入仅接收人工校对文字及真实原文锚点。

只保留通用流程与算法；这是经过提取/重构的独立工具或组件集合，并不声称完整迁移原应用、原业务质量或生产验收。源码来源、哈希与原入口映射保存在本地私有审计台账，不随此目录分发。

## 验证

tests/仅使用合成数据与临时文件，覆盖主流程、空/缺失、身份冲突和关键异常；cli.py demo可独立复现完整示例。未运行真实业务数据、联网/正式发送、原系统回归或生产部署。本项目为CLI/库，不包含Web UI。

有限PDF入口仅提取未压缩的字面字符串（含可能的元数据），有字节、CPU、深度、数量和字符预算；不是完整PDF/OCR解析器，不保证正文完整或排版顺序。压缩流、对象流、字体映射、十六进制字符串、加密和活动内容直接拒绝，需改用人工核对的文本。所有抽取结果仍是待复核线索。

## 2026-11-08 候选修订

- 修订标题必须非空字符串。
- 含deadline_time候选明确拒绝，要求显式时区后处理，不降级日期。
- 已完成事项不能直接confirm，须显式reopen。
- 收文日期规范isoformat后存储/计数/筛选。

使用包内合成样例复跑；发布集合根目录的 `verify_release.py --project workflow-task-ledger` 会依次执行测试和首次使用命令。原有版本的测试计数属于当时记录，本轮汇总以集合验证报告为准。
