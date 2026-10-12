# 认证、工作区、模型与密钥专项源码审查

- 日期：2026-10-04。
- 审查树：`E:\code\Yun_tian\the_company_agent\test5`。主树仅只读，百轮隔离验收期间不修改产品源码。
- 边界：本报告的初始结论来自源码路径复核；没有连接业务数据库、模型或外部服务，没有重启 8011，没有读取或打印部署凭据。后续修复与失败回归仅在独立工作树 ` .implementation/worktrees/major-capability-contracts` 进行，记录另追加。
- 方法：逐文件通读四个模块，并沿授权、查询范围、凭据解析、加密和实际 provider 调用核对。下方 SHA256 固定审查内容，不能用报告行号代替后续版本复核。

## 已确认的问题

### AM-001 / P1：模型新建权限可覆盖已有配置

- 位置：`backend/app/modules/model/router.py:248`、`:268`；`backend/app/modules/model/service.py:295`、`:344`。
- 证据：POST profiles 只要求中间件推导的 `models.create`，随后无条件调用 `upsert_profile`；该服务按工作区及名称找到旧行后改写 endpoint、model、enabled、capabilities。没有额外的 `models.edit` 检查。
- 触发：非管理员角色持有工作区范围的 `models.create`，但无 `models.edit`，提交已有名称。
- 后果：绕过修改权限覆盖他人模型。空 api_key 保留 secret_id；`providers/model.py:287` 的解密引用只核对工作区和密钥状态，随后调用会使用变更后的 endpoint。这放大越权覆盖的影响；没有实际发送密钥进行验证。
- 范围补充：ModelProfile 没有 owner 字段，受限数据范围可能把已有同名行隐藏；不能仅对“当前查询看得见”的旧行补检查，否则会创建运行时优先选择的同名行。
- 测试缺口：`test_model_profile_reliability.py` 的重复保存均用管理员，未测仅 create 的用户、create/edit 不同范围以及隐藏同名资源。
- 最小修复：保留管理员 POST 同名保存兼容；在工作区锁内先判定真实名称占用，已有行必须通过 edit 的资源范围校验，不能因查询隐藏而当作新建。

### AM-002 / P1：模型名称可改变 HTTP 动作权限

- 位置：`backend/app/common/authorization.py:401` 起的 leaf 推导；`backend/app/modules/model/router.py:115`、`:299`。
- 证据：通用推导将最后一段 export/test/probe/publish 等解释为动作；模型修改路由的最后一段实际是任意合法 profile_name。`_admin` 又信任该推导结果。
- 触发：仅持 `models.export` 请求 PUT profiles/export，或仅持 `models.execute` 请求 PUT profiles/probe（test 同类）。
- 后果：没有 models.edit 仍可创建或改写模型；反向情况也存在，合法 models.edit 被名称阻挡。真正 probe/export 路由不能因此失去原动作约束。
- 测试缺口：现有模型用例没有名称与保留动作词相同的非管理员 HTTP 请求。
- 最小修复：对模型路由按固定结构和 HTTP 方法解析，动态参数不参与动作推导；模型路由本身显式要求对应动作。

### AM-003 / P1：旧创建用户入口绕过管理员角色授予约束

- 位置：`backend/app/modules/workspace/router.py:98`、`:117`、`:141`；对照 `backend/app/modules/system/user_service.py:64`、`:92`。
- 证据：旧 POST workspace/users 只检查 `system.admin`（加中间件 users.manage），直接写 `WorkspaceMember(role=payload.role)`；没有 users.assign_roles 和 `_validate_assignment`。新成员接口已复用这些检查，旧创建用户入口没有复用。
- 触发：管理员向非管理员自定义角色授予 system.admin + system.users.manage，但未授予 assign_roles；其创建 role=admin 的新账号。
- 后果：新账号 membership.admin 使 `access_policy` 直接给出全部权限，绕过“仅工作区管理员能分配管理员角色”。
- 测试缺口：`test_workspace_legacy_roles.py` 已覆盖旧成员调整与末位管理员保护，但没有旧用户创建入口的最小权限角色。
- 最小修复：复用现有用户创建、角色验证及同事务审计服务；拒绝发生在写账号和成员前，保留合法管理员创建与既有响应字段。

### AM-004 / P1（条件性泄漏）：探测错误原文持久化并向模型查看者回显

- 位置：`backend/app/modules/model/service.py:487`、`:498`、`:517`、`:554`；`backend/app/providers/model.py:538`、`:762`、`:815`。
- 证据：探测捕获任意异常后把 `str(exc)[:500]` 保存为 details.error；列表 `_probe_to_dict` 原样输出 details。provider 对普通上游异常重新抛出，未保证正文不带密钥。
- 触发：上游或 SDK 错误正文包含凭据、请求头、内部地址、响应正文等敏感材料。此处不声称所有错误都含凭据。
- 后果：错误正文进入永久探测记录，后续仅 models.view 的用户可读取，不需要 secrets.view。
- 测试缺口：现有失败桩只抛固定安全文案，没有带合成秘密的异常。
- 最小修复：稳定中文错误分类与有限状态信息；原异常正文既不复制到持久字段也不复制到用户响应或详细日志。使用合成标记断言全链不含该标记。

### AM-005 / P2：自定义向量或视觉档位探测了默认档位

- 位置：`backend/app/modules/model/service.py:451`、`:464`；`backend/app/providers/model.py:689`、`:707`、`:793`。
- 证据：probe 先解析指定 profile，然后 embedding/vision 分支没有把 profile_name 传入实际调用；embed_texts 默认 embedding，analyze_image 固定 vision。最终 details.model 却取被测配置名称并记录 passed。
- 触发：新增自定义名称的 embedding 或 vision/ocr 档位，点击测试；默认档位可用而自定义档位不可用。
- 后果：出现假的“测试成功”，还消耗默认模型额度，后续业务使用选定模型时仍失败。
- 测试缺口：现有探测仅覆盖内置 embedding；宽松 `*args, **kwargs` 桩没有核对实际 profile。
- 最小修复：档位从探测传到真实 provider、传输层与用量归属，全路径一致；默认调用保持原行为。

### AM-006 / P2：服务令牌调用 logout 返回成功但令牌未撤销

- 位置：`backend/app/modules/auth/router.py:143`；`backend/app/modules/auth/service.py:271`；`backend/app/common/deps.py:163`。
- 证据：认证依赖接受 SessionToken 或 IntegrationAccessToken；logout 服务只查 SessionToken，服务令牌查不到后直接返回，路由仍返回 ok。
- 触发：用有效客户端服务令牌调用 POST auth/logout 后继续使用该令牌。
- 后果：调用方得到“成功退出”的响应，但令牌仍可使用到到期或客户端撤销。现有客户端即时失效检查不等于该退出请求已经撤销令牌。
- 测试缺口：现有会话退出与客户端撤权测试未覆盖服务令牌主动退出。
- 最小建议：按令牌类型撤销当前服务令牌；不应成功但无动作。后续已按此方案修复，见独立树回归记录。

### 追加 AM-007 / P1：按名称探测绕过模型执行的数据范围

- 位置：原快照 `backend/app/modules/model/router.py:349`、`service.py:442`；`backend/app/providers/model.py:172`。
- 证据：按名称探测只校验 models.execute，直接调用 probe 服务；provider 的 `_profile_config` 创建独立会话，按工作区查询模型，未继承请求的 models.execute 数据范围。按 ID 探测先经过受限 ctx.db 查询，但随后仍按名称解析最新行，历史同名行也需按真实生效配置核验。
- 触发：持 models.execute 且 self/部门范围、没有目标模型资源授权的用户，知道该模型名称并请求探测；或者只授权历史同名旧行而真正最新行未授权。
- 后果：能够对不在执行范围的配置发起真实外部模型请求、消耗其专用账户额度。并非跨工作区读取，也不直接返回密钥。
- 测试缺口：已有探测测试使用管理员，没有按名称与按 ID 两入口的数据范围对照。
- 最小修复：派发前查询工作区内真实生效配置 ID，再使用请求已绑定的 execute 范围核验；只取被授权的旧同名行不够。纯环境默认保持既有显式执行权限规则，不扩展业务模型选择策略。

## 已核对的保护与未扩大结论

- 末位管理员：旧 POST workspace/members 已先锁 Workspace，再经 `_validate_assignment`、`_set_roles` 和 `protect_last_administrator`；不能沿用早期“没有保护”的旧结论。SQLite 测试能确认逻辑拒绝，不能证明 MySQL 多进程锁竞争。
- 用户会话：resolve_principal 每次核对用户状态、过期、revoked_at、credential_version；改密撤销用户会话。服务令牌也重新核对客户端启用状态、工作区、创建者身份与当前 scopes。
- 密钥：Secret 路由不返回明文或密文；data_scope 在 secrets 自身域应用所有者/角色范围。运行时明确引用要求同工作区、active、未软删除；环境密钥复用还要求 endpoint 一致，不把“数据库旧密钥跟随编辑 endpoint”误写成“所有环境密钥都可跨端点复用”。
- 加密：AES-GCM、随机 nonce、带 key_version 派生；本次不读取实际主密钥，也不声称已验证部署密钥强度或完成历史主密钥轮换。
- 运行选项：runtime_options 有严格字段白名单、预算和模态校验，不能借该字段直接写任意 secret_id、请求头或上游字段。
- 未运行外部探测，不验证 SSRF、真实凭据泄漏或真实 provider 成本；此处只记录可追溯代码路径。

## 文件快照

下表由本机标准库对已审查文件计算 SHA256；主模块标为全文，依赖文件标为相关路径。测试文件只核对本专项的相关断言，不据此声称全测试库已审查。

| 文件 | 范围 | SHA256 |
| --- | --- | --- |
| `backend/app/modules/auth/__init__.py` | 全文 | `a91cd1b06b81e2a9863cc8d2e715dfb0ae3b7eb3976970bf8246d38e182b2ba6` |
| `backend/app/modules/auth/router.py` | 全文 | `6461ab26c698d42232750599e6f1e0f69c59f5c1b159785034d958afbf05d7d1` |
| `backend/app/modules/auth/schemas.py` | 全文 | `0efd17e7e4ac04e53a9e634a1f74476053cd1623ae3f02f4eb08840ec3cf013b` |
| `backend/app/modules/auth/service.py` | 全文 | `6e842bd4b74591193d895ee992b97142e540cf2f201c5ead1e779e44cd6e502b` |
| `backend/app/modules/workspace/__init__.py` | 全文 | `df043d500264d8d4ed1d3dad10872c357ea13e183fb440248a9dc433d8ac97a8` |
| `backend/app/modules/workspace/router.py` | 全文 | `1392bac47576439d335c0594909bb03bec391a406cbf650b83fc483dea7574a5` |
| `backend/app/modules/secret/__init__.py` | 全文 | `8efdc9574d66926267379a0e4d2a003a9db11dd38a8fa3748438623a2daf3c8e` |
| `backend/app/modules/secret/router.py` | 全文 | `6eaaa6ce7510f00234d91d6de1a8b181acc82a3b49cb956fb98544390461a515` |
| `backend/app/modules/secret/service.py` | 全文 | `3184c1041f042e7a466a51ff8cf447b341429cedefceafd35b85168aaef6b55f` |
| `backend/app/modules/model/__init__.py` | 全文 | `ff9569182b970c8c15d0574a061c75138b678279fdb3cd03b825888fa70771a5` |
| `backend/app/modules/model/models.py` | 全文 | `041c847e18aa2982714d7a4e905bee18b8c231190c46111a5351bb9070c0cb6b` |
| `backend/app/modules/model/router.py` | 全文 | `94634887a6ef7457cbe73493c56da71a0287b76abac8952ff7f45839fe93bb26` |
| `backend/app/modules/model/runtime_options.py` | 全文 | `46bd6d438cfaf25492f3f4fffefb3a029e35ef66d49bd2a1adaa35377c32413f` |
| `backend/app/modules/model/service.py` | 全文 | `5ab5e5020a4b469ef9ec265b39561d28f9590b5f2911ad57337d5a414177f0bc` |
| `backend/app/common/authorization.py` | 相关路径/断言 | `0e59bd64f1d87a8db08051385eb2b9df35b7893309e1435810be9442831411ca` |
| `backend/app/common/data_scope.py` | 相关路径/断言 | `b65a2867ea6de69d67dbe0d96db30b20ce8d8115e5ee3d225b87d426c2eec643` |
| `backend/app/common/deps.py` | 相关路径/断言 | `9bb500c6de6ea5e49b05705b5162ac695da6df97f5877f9beb72d86682442c1f` |
| `backend/app/crypto.py` | 相关路径/断言 | `132f359c885e23eef1279f139db6adf2def48d1fafa995df33454381c1f86ad9` |
| `backend/app/providers/model.py` | 相关路径/断言 | `7bd6e84890b0553487d1f950c847af6cb9a1bf65bf915341a949af08275f4e2b` |
| `backend/app/models.py` | 相关路径/断言 | `75a6be3332b165637751461a2b11789bf96225d4dbe42177dae2dfeb694f2f27` |
| `backend/app/modules/system/user_service.py` | 相关路径/断言 | `7b13b8c0bf4c412df1b39e64707722fc8c4e6fc1e4ce720d09bd1ed4ecadfbbf` |
| `backend/app/modules/system/service_helpers.py` | 相关路径/断言 | `0b8737e6fc4f25590c653d0b5368ec6626ad559ccdc89edbc895d4c5a070f41d` |
| `backend/app/modules/system_tools/integrations.py` | 相关路径/断言 | `ec8eba69956d3cfb1304b8e787ed0ff6905437c1743f97d8d11c234833fa6b03` |
| `tests/backend/test_workspace_legacy_roles.py` | 相关路径/断言 | `17df58b120342742172c0cf23d41f64b9b4226ea6649afecb5a8abceef2c2b97` |
| `tests/backend/test_model_profile_reliability.py` | 相关路径/断言 | `7e093e611aec8c55415e44b28429e079aca5c79f65ff7776418e24974e8aadb6` |
| `tests/backend/test_model_runtime_options.py` | 相关路径/断言 | `7ebdc2ec391c9f4b537135361fae8cb09099e3de19cdf5d8b845aabf96f3feed` |
| `tests/backend/test_identity_administration.py` | 相关路径/断言 | `579c8a544385d48e29f0b9bff6f0ebb660a54d661a741994fa52ee854f6945c8` |
| `tests/backend/test_system_foundation.py` | 相关路径/断言 | `9b9434a610e85468362d5ab91dee8ed3a4627ea040b00139baddfb000876577f` |

| `backend/app/core/observability.py` | 相关路径/断言 | `90660bce40bc2fa42f50e999f8c4e5a4c0267250870abc9e138bff28b67396fe` |
| `backend/app/modules/system/models.py` | 相关路径/断言 | `4c02782fd367105b3704d09052e59477376be167ca9abe82bd5c8830b6eb8888` |
| `backend/app/modules/system/schemas.py` | 相关路径/断言 | `bb3de6746422aed4af03f7122afe1154df72e52021863a1a6f10d3d503273562` |
| `backend/app/modules/system/admin_service.py` | 相关路径/断言 | `b800fa31acf8d3f4aa189cda023cbca97d97ea36f1b9251595eec2929f7c307e` |

## 独立工作树修复记录

- 修复树：`E:\code\Yun_tian\the_company_agent\test5\.implementation\worktrees\major-capability-contracts`。
- 状态：AM-001 至 AM-007 均已落实代码与隔离回归。
- 产品修改范围：`common/authorization.py`、`modules/model/router.py`、`modules/model/service.py`、`modules/workspace/router.py`、`providers/model.py`、`modules/auth/router.py`、`modules/auth/service.py`。新增专项：`tests/backend/test_model_and_workspace_permission_edges.py`、`tests/backend/test_auth_token_logout.py`。没有提交或推送。
- 权限修复在工作区锁内检查真实同名配置，并使用 edit 对应范围重新绑定查询；不会把隐藏的同名配置当新建。动作词模型名与真实导出/探测路由均有 HTTP 反向用例。
- 旧用户入口转用已有用户服务；保留旧四字段响应，用户名与口令规则同步为新版契约（80 字符上限、至少 10 位且含字母数字），临时口令 72 小时过期，角色/组织成员及审计同事务建立。非管理员即使同时拥有 assign_roles 也不能创建管理员。
- 视觉/向量探测显式使用所选档位；视觉 SDK 和业务传输两分支、配置解析及用量归属均有隔离断言。
- 探测失败的新写入使用有限中文分类；通过真实 provider 异常与观察日志路径注入合成秘密，验证不进响应、探测详情或日志。历史数据库/环境探测原始 error 在读取时收敛为安全提示，底层旧记录保持不变，不声称已清除历史敏感值。
- 按名称探测对真实生效 ID 做范围校验，在 provider 配置解析后再核对其实际 ID；拒绝请求不发模型调用。不扩大对普通业务模型使用的授权策略。
- 退出现在按当前传入令牌摘要查用户会话或服务令牌，仅撤销这一条记录；不会停用/轮换客户端，也不会撤销兄弟令牌。已撤销时间保留，服务层重复调用幂等；HTTP 后续使用旧令牌返回 401。没有修改公共授权或客户端批量撤销服务。

### 回归证据

1. 首组新增 19 项：修复前 **19 failed**，28.24 秒；修复后 **19 passed**，31.25 秒。失败包括 create 覆盖返回 201、动作词修改越权或合法请求被拒、非管理员创建管理员、未初始化角色、探测未传档位及错误原文回显。
2. 加入真实视觉派发、日志链及错误分类共 24 项，与 6 个既有文件联合：**78 passed**，113.28 秒。覆盖模型可靠性、运行选项、旧成员接口、系统授权、身份管理和聊天模型选择。
3. 追加 4 项（按名越范围探测 2 项、历史错误读取 2 项）：修复前 **4 failed**，6.95 秒；修复后 **4 passed**，4.76 秒。前两项实际在桩上返回 passed，后两项实际回显合成秘密；断言没有放宽。
4. 最终版本联合回归 **82 passed**，96.69 秒，进程退出码 0；专项自身 28 项，6 个既有文件合计 54 项。
5. 追加退出专项 3 项：修复前 **2 failed、1 passed**，4.49 秒；失败证据为退出后访问仍返回 200、服务令牌 revoked_at 仍为空，普通会话对照原已通过。修复后与后台客户端即时撤权、身份管理、系统授权联合：**43 passed**，59.25 秒，进程退出码 0。验证当前令牌后续 401、兄弟服务令牌与用户会话仍可用、客户端仍可签发、用户退出不会撤销服务令牌，以及直接服务层重复调用不改撤销时间。

执行器是主树绝对路径 `.venv_py312/Scripts/python.exe`，入口为独立树 `tests/run_isolated_backend.py`；全部内存 SQLite、临时 storage 和模型桩。执行器明确禁止业务数据库驱动、外网及在用 8011/8012，环境默认模型被固定为禁止连接的回环夹具。没有真实模型或业务库请求。pytest 提示的既有 datetime.utcnow/Pydantic 弃用警告不等于本专项失败。

修复过程中再次复核主树最初 28 个文件：SHA256 全部一致；之后补记 4 个相邻依赖文件。最终 32 个文件的 SHA256 仍全部一致，主树产品版本保持冻结。

### 独立树交接快照

以下为交接文件 SHA256；对应修复树，不是主树审查基线。前 6 个文件来自 82 项通过的版本且后续未变；追加的 3 个退出相关文件来自 43 项通过的版本。两个测试批次有既有用例重叠，不将它们相加为独立用例总数。

| 修复文件 | SHA256 |
| --- | --- |
| `backend/app/common/authorization.py` | `a8dd477faf94e8b4471bd405984fa34120d42a5c321781e48f15c65a7312c190` |
| `backend/app/modules/model/router.py` | `8d0e740f1f29512e7842eace9fb92f5ea1c9ec7b7aaa175d10fc2a36e3a3abcc` |
| `backend/app/modules/model/service.py` | `ff82a0e5efe1ce5d7174a54dc2a28d1ffd00b31b384288f4b095c4cbc8c7932f` |
| `backend/app/modules/workspace/router.py` | `30856c8c93aaf902970695200b312d75220c8aa246ac5629d992417724c8c3e2` |
| `backend/app/providers/model.py` | `931e3e81aea76273b7798e4668462ec0000bef8d54e55aa3343500d70ef5ffd8` |
| `tests/backend/test_model_and_workspace_permission_edges.py` | `9004a88900a0eda3af8a7e6847c58e7e406c0b183f09c9e735c8b756b1338a9d` |
| `backend/app/modules/auth/router.py` | `ec4bcd9e5b710173a6bbb05fb0949a16873453ebc4b6adbb6fe2ae40bc968fce` |
| `backend/app/modules/auth/service.py` | `4fd932810110ad3f07edaafb587f6340c9411ca2053a74e7277a97fd0dd88713` |
| `tests/backend/test_auth_token_logout.py` | `f97639d50bde5f26589d8257a3b8a1b3c852597325453391fc088a367ab0e204` |
