# Yeelight Pro for Home Assistant - maintenance fork

基于 [hasscc/yeelight-pro](https://github.com/hasscc/yeelight-pro) 的维护分支。
当前版本 **0.1.0**，按用户提供的局域网协议 **v2.4 (2025-07-04)** 适配。
保留 `yeelight_pro` 域及原有 Mesh 单设备实体唯一标识。

支持 HA **2026.11+**，已在 HA **2026.11.2 / Python 3.14** 上执行模拟协议测试。
测试通过不等于所有型号均完成实物验收。协议覆盖和限制见
[协议适配说明](docs/protocol-v2.4.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。

### 安装与接入

1. 备份原组件与 HA 配置。HACS 可添加本仓库为自定义集成，或者手动复制
   `custom_components/yeelight_pro` 到 HA 配置目录同一路径。
2. 重启 HA Core，在易来 Pro App 的目标网关设置里开启局域网控制。
3. 在 HA“设置 → 设备与服务 → 添加集成”中选择 Yeelight Pro，填写网关 IP；
   普通网关选 Gateway Pro，Wi-Fi 全面屏选 Wifi Panel。
4. 先验证状态和在线信息，再验证少量设备控制。原 Matter 配对和自动化引用
   不会自动迁移；同一物理功能应明确选择一个控制来源。

无需易来云账号、密码或 Token，TCP 默认端口为 **65443**。
UDP 发现有响应不能证明 TCP 控制服务已开启。请勿同时安装其他
`yeelight_pro` 同域组件。

### 维护与测试

```sh
docker run --rm --entrypoint python -v "$PWD:/work" -w /work \
  ghcr.io/home-assistant/home-assistant:2026.11.2 \
  -m unittest discover -s tests -p test_protocol_v24.py -v
```

测试只使用合成设备标识与本地模拟网关。请勿向仓库提交家庭配置、抓包、
真实设备标识、地址、密码或协议 PDF。回滚时恢复先前组件并重启 Core；
保留原集成配对及配置，不通过删除设备来“回滚”。

### 协议命令入口

`yeelight_pro.send_command` 保留 `host/method/params`，新增协议原生顶层
`nodes/scenes`。写操作会检查已知只读字段、值域和动作格式；ACK 代表网关
接收，不代表设备到位。下例标识为虚构，使用前替换为自己网关返回的节点。

```yaml
action: yeelight_pro.send_command
data:
  host: "gateway.example"
  method: gateway_set.prop
  nodes:
    - id: 42
      nt: 2
      duration: 2000
      set:
        p: true
        l: 50
```
