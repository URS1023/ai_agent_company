# 固定的 DeepSeek Harness 源码

`deepseek-harness-0.1.7-alpha.2.tar.gz` 是用户提供源码的逐文件快照，供新克隆直接重建 DSH 运行环境，不依赖开发者本机路径。共 13,244 个文件，源码路径、大小及 SHA-256 位于 `services/deepseek_harness/UPSTREAM.json`；压缩包摘要位于本目录 `deepseek-harness-source.json`。

来源为 DeepSeek Harness 项目，许可证为 MIT，原始 LICENSE 和第三方声明完整保存在压缩包中。该本地快照没有 Git 元数据，不能将清单中的参考 commit 宣称为已验证的整树 commit。

安装：`python scripts/install_harness.py`。安装器校验压缩包及每个文件，在 `.implementation/harness_migration/upstream` 解包，依上游锁文件安装 pnpm 依赖并构建。可用 `--unpack-only --target <空目录>` 单独验证交付源码，或用 `--source <原始目录>` 校验并复制已有快照。

上游升级时先重新审查 DSH API 和模型协议，再更新完整清单与源码包，不能只修改版本号。`scripts/package_harness_source.py` 按固定清单重新产生归一化文件时间、权限和顺序的压缩包。
