# B17 网络证据保存器修复（实现与绿色专项保存在 B18 独立树）

## 原始故障和可证明结论

- 原始九域运行：B17 `.implementation/full-business-browser/20261004-134232-0cad7d05/manifest.json`，HEAD `4f47d51eb2deb1de7e39dccd21310c9133e1da8e`。system/content 通过，capability 于 13.204 秒失败，exit=1，Windows Job active_after=0；后六域未执行。
- `001-capability/browser.log:5–19` 显示 Windows Node v24.16.0 的 `fs.renameSync(network.json.tmp, network.json)` 抛 `EPERM`，syscall=rename，errno=-4048；调用链是 blocked CONNECT 的 `authorize → save → saveJson`，尚未创建上游连接。
- 当次 guard 原字节 SHA256：`caf30453324fc52f48eb2685e932a731b41abec9d1d0dbcbe7d58cd134db7626`。
- 原 browser.log SHA256：`66d0619bb8be16ebe7b9380452f3ee557c0fc9477c4d5ba5dbb781b9119a4378`。
- 原 network.json SHA256：`e8229b2afb547b7c3ab13f9516ab1cb6559dac95fc28ce0eb4f16fdc00537b43`。读取时为 873 字节，Archive、非只读，完整 JSON，allowed=0/blocked=5/background=5；原 `.tmp` 已不存在。父目录为普通 Directory。
- 原代码使用固定 `.tmp`，同步写入和 rename，一次失败立即退出。原 `process.on('exit', save)` 可能再次写同名临时文件；不能用现存 `.tmp` 缺失反推首次失败已成功或具体占用者。
- 实际入口 `tests/dsh/business_browser_acceptance.py:232–271` 每域串行创建一个 Node、每域独立报告路径；父进程先回收 Node/Host，再于第 192 行读取报告。已读调用链未发现多 writer、跨域共写或父读子写竞争。因此固定临时文件是通用脆弱点，不能声称已经证实它造成这次 EPERM。
- 证据只能确认本地替换失败。没有句柄归属、ACL、底层 Windows 错误详情或现场重试结果，**未证明杀毒软件、编辑器、短时锁、永久权限错误中的任何特定原因**。

## 工作树迁移与短暂写入记录

1. 原授权指定 B17，并称正在运行的 gate 不使用此 guard。先在 B17 新增本专项测试，用原 guard 执行三个独立有界基线检查，保存下表原件。
2. 基线其他错误对照日志最后写入于 `2026-10-04T05:48:44.1282847Z`。此后新增 writer 并修改 B17 guard；未运行修改后的绿色测试。
3. 根代理随后更正：chart_precision_browser 和 numeric_statistics_display 两个后续阶段也 require 此 guard。收到更正后立即停止 B17 实现，保存本轮三个文件和日志到 B18。初次恢复校验因 git blob 的 LF 与工作树 CRLF 导致 SHA 断言失败，发生在任何写入之前。
4. 使用 B18 尚未修改、SHA 与原 B17 完全相同的 guard 原字节，与 git blob 做换行归一化后再次核对；于 **`2026-10-04T05:50:18.699315Z`（13:50:18 +08:00）** 恢复 B17 guard 为原 SHA。本轮两个新增文件的固定绝对路径均在 B17 `tests/ui`，B18 保存同字节后才逐一删除；无递归迁移、无其他文件回滚。
5. 未记录初次 guard 写入的单独时钟值，其可证时间窗口为上述 `05:48:44.1282847Z` 与 `05:50:18.699315Z` 之间，不能伪造更精确时间。根代理更正时报告 gate 位于 backend-platform-contracts 88%，首次显式使用该 guard 的 chart/numeric 尚未开始。
6. 恢复后 B17 `git status` 仅有根代理新增的正式故障记录 JSON；guard 再次只读 SHA 核对一致。本轮未动产品和原 21 个冻结文件，但不能写成整棵 B17 从未短暂变更。此后实现、复核和绿色专项全部在 B18 `major-natural-continuation`。

## 最小修复

- 新增 `tests/ui/network_report_writer.cjs`，guard 的同步 save 入口改为该 writer。每次保存一次序列化、一次 `writeFileSync(..., flag='wx')`，使用同目录 PID+UUID 唯一临时文件。
- 始终通过真实 rename 替换目标，**不提前删除、截断或直接覆盖旧有效 JSON**。重试复用同一份完整临时快照，不重复更新网络计数。
- 只在 Windows、错误码为 EPERM/EBUSY、syscall=rename、源/目标精确匹配本次路径、临时源与现有目标均为可写普通文件时进入候选重试；目标尚不存在也可替换。EPERM 仍只是候选，代码和记录不声称已识别具体共享占用。
- 最多 4 次 rename，间隔固定 10/25/50 ms，主动等待总上限 85 ms；无 HTTP、SQL、模型或整套重试，不扩大任何套件预算。
- EACCES、ENOSPC、非 Windows、错误 syscall/目标、只读目标和无法核验文件状态立即抛出。写入或序列化错误也没有重试路径。
- 最终失败保留原错误及 `reportTemporary/reportDestination/reportRenameAttempts/reportRetryDelayMs`，保留临时原件并锁存失败；后续 save/exit 不再写入或重试，不能覆盖原失败证据。正常真实进程因此非零退出，旧报告不能令其被报告为成功。
- `close()` 的 finally 只确保释放代理、套接字及退出监听器，原保存异常仍向调用方抛出。授权顺序未改：计数 → 同步保存成功 → 返回允许目标 → 上游连接；失败不能提前转发。

## 原始红例与对照

三个基线命令分别在产品/guard 未修改的 B17 执行一次：

```text
node --test --test-name-pattern=瞬态占用 tests/ui/network_report_writer.test.cjs
node --test --test-name-pattern=持续占用 tests/ui/network_report_writer.test.cjs
node --test --test-name-pattern=非占用错误 tests/ui/network_report_writer.test.cjs
```

| 原始文件 | 结果与边界 | SHA256 |
|---|---|---|
| `.implementation/network-writer-batch17-red-transient.log` | 1 failed；第一次受控 EPERM 就抛出，尚未到可恢复的真实 rename；144.0242 ms | `7e30c98a409b8f12675afa727ef2ff04b3675b5e075caec9efa2292b6144f89c` |
| `.implementation/network-writer-batch17-red-persistent.log` | 1 failed；实际 1 次，期望固定上限 4 次；原目标保持完整；144.5031 ms | `e08207a6289eafa824bcb21b75ddeb9c2960a8faa5a9e6447b8552ad03524a00` |
| `.implementation/network-writer-batch17-baseline-other.log` | 1 passed；ENOSPC 立即抛出且保留旧目标，这是原本已正确的对照；157.5771 ms | `bbf42412eb0f74b9b1277cbc05909114755980916464126467db2eac836ed096` |

以上原件留在 B17 ignored 目录，同字节复制到 B18；没有把“原目标已被破坏”当作本次红例。受控故障由真实 fs.renameSync 外的短注入产生，测试仍使用真实临时文件、真实序列化和成功时的真实 rename，不复现或声称捕获到了现场占用来源。

## B18 首轮绿色专项

唯一修后命令：

```text
node --test tests/ui/network_report_writer.test.cjs
```

- **13/13 passed，0 failed/cancelled/skipped，1181.2483 ms**；命令进程总计 1.507 秒，退出码 0。
- 13 项包括：瞬态 EPERM 2 次后成功（3 rename）、持续 EPERM 4 次后停止、ENOSPC 立即抛、瞬态 EBUSY、EACCES/异 syscall/异目标/只读/非 Windows 五种立即停止、唯一临时路径及首次创建，以及 HTTP/CONNECT/WebSocket 三种保存失败不转发。
- 持续失败三种集成检查在独立 Node 子进程运行，仅请求本轮随机端口代理一次。三个子进程均 exit=1、rename=4、上游连接/请求=0，内存允许尝试计数=1、旧目标计数=0、保留临时文件计数=1；exit 没有再次重试。
- 临时原件由专项读回内容并核对路径后清理；正式 writer 在失败时不会自动删除原件。测试日志保留临时路径和断言结果，不将测试后已清理的临时路径描述为长期可下载证据。
- 绿色日志 `.implementation/network-writer-batch17-green-first.log` SHA256：`089511376123c3535a8023c01b628aa3c8270a84042d6499cacd27bce9878d0a`。
- `node --check` 两个新文件及 `git diff --check` 通过。未跑浏览器、后端组、业务源、真实模型、外网或整套 gate；本地合成代理请求不构成真实业务重试。

## 冻结指纹与验收限制

| B18 文件 | SHA256 |
|---|---|
| `tests/ui/isolated_browser_guard.cjs` | `dc9d11ff43a7192801c06a0edbb2af8d6ba301df5bffbf5585e978b8fa57d644` |
| `tests/ui/network_report_writer.cjs` | `37e8d8b543bd930783f522bf143881e10c35c75418cb99aa58098ac3b396ebae` |
| `tests/ui/network_report_writer.test.cjs` | `aa1af93defaf534d072e785da41fce39b4d70a63b9d0a1d9814b596a488625b8` |

本次修复完成于 B18，尚未移动回 B17。B18 此前冻结的 PPT schema 两文件指纹仍为 `bb6e2bf01be72a4694a1b4534404adb67eac5718e41730e95bff978b2557d713` 与 `5d0215d2c2d21f10273d9952700c2787d89b80e96c844388ff4da91ac852b1a8`，没有改写。另一代理的自然续办产品和测试也未修改。

后续由根代理在 B17 gate 结束后精确移入三个 harness 文件，再独立复验相关图表阶段和完整九域。本次 13 项不能代替该浏览器验收，也不能把旧 guard 的 gate 结果宣称为新字节完整验收。未修改正式记录、门禁或 README，未提交/推送。

探索代理对最终三文件只读复核，未发现当前 Windows 范围的实质缺陷或测试假阳性；没有新增测试次数。上述有限重试断言依赖 Windows，本轮不是跨平台验证，也不证明原现场文件占用已经解除。

## 根代理移入与受影响阶段复验

以上保留实施阶段原始状态。2026-10-04 06:03:21Z，根代理确认旧守卫下第二轮19阶段门禁全部通过、原21份冻结无变更后，将上述三文件按原始SHA精确移入B17。独立补验在06:03:22–06:03:39Z完成：保存器13项、图表浏览器13项、数字显示14项全部通过，阶段进程分别1.954/10.468/5.016秒。见 `network-writer-validation-batch17.json`，含移入回执、各原始日志SHA和命令。本次重复保存器13项不与B18专项相加为26个独立用例。

九域完整第三轮仍需另行开始和登记；旧门禁使用原守卫，新守卫的补验是单独证据，二者不冒充同一份源码上的一次全量执行。
