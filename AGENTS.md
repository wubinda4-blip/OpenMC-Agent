# Codex Repository Rules

这些规则适用于本仓库内的 Codex 工作流。

> **本仓库默认开启自动 commit/push**：测试通过后即自动提交并推送本次任务相关文件。此项目级偏好**显式覆盖**全局 `~/.claude/CLAUDE.md` 中"不自动提交除非明确要求"的默认设定；在本仓库内工作时以本规则为准。

## 默认工程流程

- 修改代码前先检查当前工作区状态，区分本次任务改动和用户已有脏文件。
- 代码改动完成后，运行最相关测试；能跑全量 pytest 时优先使用：

```bash
conda run -n openmc-env python -m pytest -q
```

- 如果只是文档或规则变更，可运行针对变更范围的轻量检查，例如 `git diff --check -- <paths>`，并在最终回复中说明未跑全量测试的原因。
- 测试通过且确认改动范围无误后，自动执行：

```bash
git add <本次任务相关文件>
git commit -m "<简短说明>"
git push origin <当前分支>
```

- 自动 commit/push 时必须只 stage 本次任务相关文件，不得把无关的用户脏文件、临时脚本、PDF、大型输入资料或未确认数据一起提交。
- 如果测试失败、认证失败、远端 push 被拒绝、工作区混有无法判断归属的改动，停止自动提交并向用户说明阻塞点。

## 文档维护

- 每次完成代码改动后，同步检查是否需要更新 `README.md`。
- 每次完成重要功能、架构、检索流程、评估流程或能力边界变更后，维护 `docs/project_technical_report.md`：
  - 更新当前状态；
  - 更新验证结果；
  - 更新风险/边界；
  - 更新下一步建议；
  - 在维护记录中追加变更摘要。
- 相关 strategy 文档也要保持与代码一致；过时文档应合并后删除。

## 安全边界

- 不读取或输出 secrets、tokens、private keys。
- 不把 RAG / GraphRAG / 文档 evidence 当作材料密度、composition、核数据库路径、benchmark 常数或真实 loading map 的事实确认来源。
- 不改变 renderer 能力边界；unsupported subsystem 仍应保持 skeleton 或 human confirmation。
