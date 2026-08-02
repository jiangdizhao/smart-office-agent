# 访客服务语义理解与结果中心管理员密码

## 功能行为

访客服务语音入口采用两级路由：

1. 明确短语由本地规则立即识别，不调用通用聊天。
2. 未命中的自然表达交给 GPT Realtime 做受限语义分类。

GPT Realtime 只能返回以下固定枚举：

```text
contact
recording
transcript
results
none
```

它不能生成 URL、JavaScript 或任意工具名称。

典型表达：

```text
我想登记                         -> contact
我想留下联系方式                 -> contact
帮我们把接下来的谈话录下来       -> recording
刚才我们说了什么                 -> transcript
让我看看已经保存的客户资料       -> results
```

高置信度结果直接打开对应界面；中等置信度先由 Sara 询问确认；低置信度继续进入普通对话。

`results` 只打开管理员密码界面，不直接显示数据。

## 配置管理员密码

密码只存在于启动 Backend 的 Windows 环境中，不应写入 Git 仓库或 Vite 前端变量。

### 简单配置

在启动 Backend 的同一个 PowerShell 窗口执行：

```powershell
$env:SMART_OFFICE_ADMIN_PASSWORD = "请替换为实际密码"
```

然后启动 Backend。

### 推荐配置：PBKDF2 哈希

生成哈希：

```powershell
cd D:\smart-office-agent
python .\backend\scripts\hash_result_center_admin_password.py
```

脚本会隐藏输入。复制输出的完整哈希，然后设置：

```powershell
$env:SMART_OFFICE_ADMIN_PASSWORD_HASH = "pbkdf2_sha256$..."
Remove-Item Env:SMART_OFFICE_ADMIN_PASSWORD -ErrorAction SilentlyContinue
```

再启动 Backend。

### 会话有效期

默认管理员会话有效 600 秒：

```powershell
$env:SMART_OFFICE_ADMIN_TOKEN_TTL_SECONDS = "600"
```

允许范围为 60–3600 秒。关闭结果中心面板时，前端会立即调用登出接口并删除当前会话令牌。

## 保护范围

以下接口需要管理员会话：

```text
GET  /api/result-center/contacts
GET  /api/result-center/contacts.csv
GET  /api/result-center/recordings
GET  /api/result-center/artifacts/{filename}
POST /api/result-center/open-output-directory
```

管理员接口：

```text
POST /api/result-center/admin/login
GET  /api/result-center/admin/status
POST /api/result-center/admin/logout
```

密码不会：

- 发送给 GPT Realtime；
- 写入会话记录；
- 写入 URL；
- 写入浏览器日志；
- 保存在 Git 仓库。

连续输入错误密码 5 次后，该客户端暂停验证 30 秒。

## 验收

依次测试：

```text
我想登记
我想留下资料
帮我们录一下
刚才我们说了什么
让我看看保存的资料
```

最后一条应只显示管理员验证界面。错误密码不得显示任何联系人或录音；正确密码才能进入结果中心。关闭结果中心后再次打开，应重新要求密码。
