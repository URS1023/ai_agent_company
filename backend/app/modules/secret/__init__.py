"""密钥（secret）模块包：加密保存第三方凭据 / 数据库口令，对外只暴露引用 ID。

明文只存在于服务端内存：``service.py`` 用 AES-GCM 加密后落库（``ciphertext`` / ``nonce`` /
``key_version`` 三元组缺一不可），业务侧（capabilities、数据源等）只保存并传递 ``secret_id``。

对外路由前缀 ``/api/secrets``（见 ``router.py``），读 / 建 / 删分别要求 ``secrets.view`` /
``secrets.create`` / ``secrets.delete``；查询与写入均限定在当前 ``workspace_id`` 内，
且任何响应都不回显明文或密文。
"""
