# WFUConnect

潍坊学院校园网（gwifi 门户 `http://210.44.64.60`）断线自动重连脚本。

网关掉线时脚本会自动重新登录校园网，适合宿舍路由器、树莓派或常开主机长期挂着。
纯 Python 实现，无浏览器依赖，协议细节逆向自门户的 `source/login.html`。

## 特性

- **断线自动重连**：守护模式周期性探测网络，一旦掉线立即重新认证。
- **多路探测**：内置门户在线接口 + 多个 captive portal `generate_204` 备用探测，避免误判。
- **失败分类**：识别账号侧问题（未开通 / 需绑定手机号 / MAC 变更等），这类错误不无脑重试。
- **三种动作**：`run` 守护、`status` 单次查询、`login` 立即登录一次（可 `--dry-run`）。
- **开箱即用的命令**：安装后直接 `wfuconnect login`，无需 `python xxx.py login` 这类脚本路径调用。
- **配置灵活**：命令行参数 > 环境变量 > `config.json`，密码文件已被 `.gitignore` 忽略。

## 环境要求

- Python **>= 3.14**
- 运行时依赖：`requests`、`pycryptodome`（见 `requirements.txt`）

## 安装

### 方式一：从 PyPI 安装（推荐）

```bash
# 装成全局命令，任意目录直接使用
uv tool install wfuconnect      # 或 pipx install wfuconnect

# 或者装进当前虚拟环境
pip install wfuconnect
```


卸载：`uv tool uninstall wfuconnect`。




## 配置账号

在**运行 `wfuconnect` 的目录**下新建 `config.json`：

```json
{
  "username": "你的学工号或手机号",
  "password": "你的密码",
  "base_url": "http://210.44.64.60",
  "interval": 15
}
```

从源码安装时，也可以直接复制示例配置：`cp config.example.json config.json`。

`config.json` 含明文密码，请勿提交到 git（仓库已通过 `.gitignore` 忽略）。
配置文件按 **`WFU_CONFIG` 环境变量 > 当前目录 > 项目根目录** 的顺序查找；
也可以完全不写配置文件，改用环境变量或命令行参数（见下）。

## 用法

安装后（激活虚拟环境，或使用全局安装），直接调用 `wfuconnect`：

```bash
wfuconnect status                    # 查看当前是否联网（exit 0=在线，1=离线）
wfuconnect login                     # 立即登录一次
wfuconnect login --dry-run           # 只打印将要发送的密文，不提交
wfuconnect run                       # 守护模式：断线自动登录（默认动作）
wfuconnect run --interval 10 -v --log-file wfu.log
```


### 参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `action` | `run` 守护重连 / `status` 查询状态 / `login` 登录一次 | `run` |
| `-u, --username` | 学工号 / 手机号 | — |
| `-p, --password` | 密码（建议改用 `WFU_PASSWORD` 环境变量） | — |
| `--base-url` | 门户地址 | `http://210.44.64.60` |
| `--interval` | 断线检测间隔（秒） | `15` |
| `--timeout` | 单次请求超时（秒） | `10` |
| `--max-attempts` | 一轮重连的最大尝试次数 | `5` |
| `--dry-run` | 只加密不提交（仅 `login`） | — |
| `-v, --verbose` | 输出调试日志 | — |
| `--log-file` | 同时把日志写入文件 | — |

### 环境变量

| 变量 | 对应参数 |
| --- | --- |
| `WFU_USERNAME` | `--username` |
| `WFU_PASSWORD` | `--password` |
| `WFU_BASE_URL` | `--base-url` |
| `WFU_INTERVAL` | `--interval` |
| `WFU_CONFIG` | 指定 `config.json` 的路径（默认按「当前目录 -> 项目根目录」查找） |

读取优先级：**命令行参数 > 环境变量 > `config.json` > 内置默认值**。

## 后台运行

Linux / macOS：

```bash
nohup wfuconnect run --log-file wfu.log &
```

Windows（开机自启可配合任务计划程序）：

```powershell
chcp 65001
wfuconnect run --log-file wfu.log
```

> 还没安装 `wfuconnect` 命令时，把上面的 `wfuconnect` 换成 `python -m wfuconnect` 即可。

## 认证协议

逆向自 `source/login.html`：

1. `GET /gportal/web/login`，从 HTML 中取出 `#frmLogin` 表单字段
   （`nasName` / `userIp` / `sign` / `iv` / `redirectUrl` / `portalTemplateId` …）。
2. 明文 = jQuery `form.serialize()` 的结果，追加 `name=<账号>&password=<密码>`。
3. AES-128-CBC + **ZeroPadding**，密钥硬编码为 `1234567887654321`，
   IV 取页面隐藏域里的 16 位十六进制字符串（按 UTF-8 文本当 16 字节使用），密文转 Base64。
4. `POST /gportal/web/authLogin?round=<随机数>`，body 为 `data=<Base64>&iv=<明文iv>`；
   响应 JSON 中 `status == 1` 即认证成功。

`sign` 与 `iv` 每次打开登录页都会变，所以每次登录都要重新抓取页面；会话靠 Cookie `PHPSESSID` 维持。

门户返回的失败原因码：

| reasoncode | 含义 |
| --- | --- |
| `30` | 账号不存在或未开通 |
| `32` | 需要先设置密码 |
| `40` | 需要先完善个人信息 |
| `41` | 需要先绑定手机号 |
| `43` | MAC 地址变更，需要重新绑定 |

## 项目结构

```
wfuconnect/          包目录
  __init__.py        包信息（版本号）
  __main__.py        支持 python -m wfuconnect
  cli.py             CLI 入口：参数解析、日志、守护循环（Watchdog）
  portal.py          PortalClient：抓页面、加解密、登录、在线检测
pyproject.toml       包元数据 + wfuconnect 命令入口 + 构建后端
config.example.json  配置示例
requirements.txt     pip 依赖清单
```

自检加解密逻辑：

```bash
python -m wfuconnect.portal
# 输出 selftest ok
```

## 故障排查

- **Windows 控制台乱码**：先执行 `chcp 65001`，或在 Windows Terminal 中运行。
- **总是提示未连接**：确认 `--base-url` 可达；`-v` 查看详细日志。
- **报 “账号状态异常”**：属于账号侧问题（未开通 / 未绑定手机号 / MAC 变更），脚本会暂停重连，请按上表人工处理。
- **门户结构变更**：若报 “未找到 frmLogin 表单”，说明门户页面已改版，需要更新 `wfuconnect/portal.py` 中的解析规则。
