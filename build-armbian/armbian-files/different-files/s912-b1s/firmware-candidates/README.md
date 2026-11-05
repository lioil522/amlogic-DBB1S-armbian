# Ampak 蓝牙补丁候选

`BCM4345C0-ampak.hcd` 来源于本机原厂 Android 解包固件的 `system/system/etc/bluetooth/BCM4345C0.hcd`。

- 大小：65639 字节，338 条完整 HCI 下载命令。
- 内嵌标识：`BCM4345C0 Ampak UART 37.4 MHz BT 4.1-0143`。
- SHA-256：`d657de90cbc6e39589688881bbbc39599c4c1d0dac064cbfb1390fa0da6f3fb7`。

它是等待 A/B 测试的候选，不在 `rootfs/` 下，镜像构建不会自动应用。测试时保留当前 `/usr/lib/firmware/brcm/BCM4345C0.hcd`；按芯片期望名称 `BCM4345C0.hcd` 安装候选，并核对 initrd 是否也有副本。重新初始化后的日志应显示 Ampak 标识，再验证 BLE、经典蓝牙配对、音频、断线重连和冷启动。
