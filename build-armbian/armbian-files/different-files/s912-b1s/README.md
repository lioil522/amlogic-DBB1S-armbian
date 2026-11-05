# 当贝 B1S 板级适配

## 当前交付状态

截至 2026-11-05，`6.18.48-b1s-pm1` 已安装并启动，HDMI 显示/音频、红外遥控、GPU 渲染和 USB host/device 均已回归。正式镜像仍待实机启动验收；不能以静态核查替代启动结果。

- 电源键：保留 GPIOAO_2 低有效、`gpio-keys-polled`、20 ms 上拉描述；本机无实体键，电源验收实际走红外。方向由驱动的 `GPIOD_IN` 设置；该版本 Meson pinctrl 不支持 `input-enable`。
- GPU：Mali-T820 绑定到现有 80°C passive trip；保留原 CPU 温控、90/110°C 阈值及 GPU OPP。
- HDMI：GPIOH_3 低有效，删除 `enable-active-high`；connected、EDID、固定 1080p 显示和 I2S 音频已实测通过。镜像沿用平台 `uEnv.txt` 的 `video=HDMI-A-1:1920x1080@60e`，开机服务选择 `AIU HDMI CTRL SRC=I2S`，播放使用 PCM device 0。
- USB：已验收的 GPIOAO_5 高有效 VBUS 纳入默认 stage1，连接到 Meson glue 的 `vbus-supply`；不设置 always-on 或强制 host，保留 OTG/DWC2 peripheral。默认 host、手动 device，原机口 gadget 双向传输已通过。PHY PM 补丁已应用，但 DWC2 开机 probe/timeout 和切换后的 xHCI 故障仍未根治，详见 `kernel/README.md`。
- 遥控：实测 `0x8102` necx 十二键码表及加载服务纳入 rootfs；释放帧 `0x8102ff` 不映射，语音键为 `KEY_VOICECOMMAND`，不代表麦克风输入已经验收。
- ADC：现场确认本机完全没有实体键（AV 口也无复位键），本项不适用。`stage1` 和 `no-adc-keys` profile 禁用未装配的 `/adc-keys`；保留基线节点作为参考，其他单项 profile 仍拒绝未标定的 ADC-enabled 内核。
- 蓝牙：原厂 Ampak HCD 保存在 `firmware-candidates/`，等待与现有版本做配对、重连和音频对比。

新内核候选 `6.18.48-b1s-pm1` 已完成 Image、2956 个模块和三个 deb 构建，
套件内容核查及隔离 headers 编译通过，详见 `kernel/README.md`。
**pm1 已实机启动，USB gadget 双向传输通过；GPU 自动休眠/调频和 DWC2
开机探测顺序仍有已知问题。此前完整验证镜像使用原内核，不包含新 PHY PM 补丁。**

## DTS 来源与版本

仓库基线：`3601399fe3e543b31fd5d1ace55d659ede6c141b`。原可启动 DTB SHA-256：

```text
7a8e99758a54d22c670737d5226058f37056b360d4ea29f556cda19eda7280a0
```

`dts/meson-gxm-dangbei-b1s-baseline.dtsi` 从此 DTB 恢复符号和引用，基线编译结果按节点路径解析 phandle 后语义一致。原文件作为 `tools/b1s/fixtures/known-good.dtb` 保留。`tools/b1s/dtb_tools.py` 显式解析 clock/GPIO/PHY/thermal/MDIO/shmem 等引用，避免将普通整数误当成 phandle；它针对本板已有 bindings，并非通用 DT schema 验证器。

内核参考：[`ophub/linux-6.18.y@e0497a975383cedc8b8d8a7c9e84eac2e2238a6e`](https://github.com/ophub/linux-6.18.y/tree/e0497a975383cedc8b8d8a7c9e84eac2e2238a6e)（`update to v6.18.48`，构建日期前的版本提交）。此源码用于核对驱动与 binding；尚未证明它连同某一补丁集能逐字节复现正在运行的内核。

与该提交的 `meson-gxm.dtsi` + `meson-gx-p23x-q20x.dtsi` 比较，原 DTB 还使用旧版 PWM、MDIO mux、HDMI clocks 和 pin bias 描述。首轮保留恢复出的公共部分作为冻结基线。迁移到该公共 DTSI 应另行验证这些差异，不能只通过 include 替换引入网口/时钟变化。原厂 Android 3.14 DTS 未直接混入主线设备树。

当前 DTC 1.6.1 有 5 条继承自基线的结构告警（两个 pinctrl 的 unit-address/simple-bus，GPIO IRQ 控制器的 address-cells）；新片段没有引入新的告警类型。

## 编译

依赖：`device-tree-compiler`、Bash；回归另需 Python 3 和 `jq`。

```sh
board=build-armbian/armbian-files/different-files/s912-b1s
bash "$board/build-dtb.sh" core /path/to/meson-gxm-dangbei-b1s-core.dtb /path/to/config-6.18.48-ophub
bash "$board/build-dtb.sh" stage1 /path/to/meson-gxm-dangbei-b1s-stage1.dtb /path/to/config-6.18.48-ophub
python3 -m unittest discover -s tools/b1s -p 'test_*.py' -v
```

| profile | 内容（均为完整 DTB） |
|---|---|
| `baseline` | 原始语义，用于比较 |
| `power-key` / `gpu` / `hdmi` | 原始基线加单项修复 |
| `core` | 电源键 + GPU |
| `stage1` | 电源键 + GPU + HDMI + USB VBUS + 禁用未装配 ADC 键，仓库默认源码 |
| `no-adc-keys` | 基线加 ADC 节点 disabled，本机无实体 ADC 键 |
| `usb-vbus` | 原始基线加已验收 USB VBUS，保留单项隔离比较 |

配置参数可省略以仅编译 DTB；镜像构建必须提供目标内核 config，缺驱动时失败。本机无需 ADC 专用内核；`kernel/adc.config` 仅保留为以后加装按键的配置参考。

## 镜像构建

`rebuild` 在内核 DTB 解包和板级内核调整后应用板级 bootfs，再运行 `install-dtb.sh` 从源码编译 B1S DTB；之后才执行清理。记录位于镜像内：

- `/boot/b1s-build-manifest.txt`、`/usr/lib/b1s/build-manifest.txt`：内核、profile、仓库 HEAD、DTS/板级 rootfs/安装脚本源码哈希、DTB/config 和最终运行配置清单哈希。
- `/usr/lib/b1s/firmware.sha256`：清理完成后的 Broadcom 固件哈希。
- `/usr/lib/b1s/runtime-assets.sha256`：最终码表、服务和三个脚本的哈希，可在镜像内以 `cd / && sha256sum -c /usr/lib/b1s/runtime-assets.sha256` 核对。
- `/boot/b1s-runtime-audit.json`、`/usr/lib/b1s/runtime-audit.json`：离线依赖核查、目标软件包版本、服务启用链接和模块哈希。

### 用户态配置与依赖

`copy_files` 复制板级 rootfs；内核安装及模块精简/depmod 完成后，打包前运行
`install-runtime.sh` 安装码表、两个 oneshot 服务与三个 `/usr/local/sbin/b1s-*`
脚本，并以相对符号链接启用遥控和 HDMI 音频服务。只启用、不启动目标服务，
不对构建主机执行 daemon-reload，不自动切 USB 角色或创建 gadget。

输入镜像必须已有 **ir-keytable、alsa-utils、kmod、systemd**。脚本离线检查
目标 dpkg 状态、可执行文件、驱动 Kconfig、模块文件/索引及依赖闭包；
支持在 x86 上核查 ARM64 rootfs，不 chroot、不执行目标程序。
缺依赖时明确失败，不能生成“构建成功但遥控/音频/gadget 不可用”的镜像。
准备基础镜像时可安装 `apt-get install ir-keytable alsa-utils kmod systemd`；
本次归仓不自动联网安装、不修改当前系统。`CONFIG_USB_MASS_STORAGE` 必须为
`m`，DWC2 必须支持 peripheral/dual role，拒绝 host-only 和内建自动 gadget。

归仓版脚本对实测配置只做启动等待/安全性调整：红外按驱动查找 rc 编号并重试，
服务不再 `After=multi-user.target`（避免与默认 Before 形成环）；HDMI 在
alsa-restore 后设置 I2S，二者显式 15 秒超时。当前盒子上的旧脚本与服务未替换，
**新脚本的实际开机、切角色和最终镜像仍需后续实机回归**。

### USB 角色安全用法（归仓版）

原机 USB-A 为 `1-2`/PHY1/OTG，补焊口为 `1-1`/PHY0/host-only。
device 必须接原机口。切换可能影响两个口，不能保证补焊 host 口继续工作。

1. 卸载/swapoff USB 介质并拔掉两个口的外设，确认 A-A 线尚未连接。
   若系统从 USB 启动，不要在该系统内切角色；工具会拒绝仍挂在控制器上的存储。
2. `b1s-usb-role device --confirm-disconnected`，确认 UDC 出现。
3. 手动加载 gadget，使用**独立、未挂载的测试镜像文件**作为 backing file；
   不导出正在使用的文件系统、整盘 eMMC 或恢复盘。此后才接原机口到电脑。
4. 电脑安全弹出、拔掉 A-A，卸载 gadget（如 `modprobe -r g_mass_storage`），
   再执行 `b1s-usb-role host --confirm-disconnected`。
5. 若 host 口仍失效，保持两个口都拔空，再显式执行
   `b1s-usb-role host --confirm-disconnected --recover-xhci`，对 DWC3 unbind/bind。
   这是已有故障的恢复操作，不是根治；失败会返回非零并提示，不能忽略。

`b1s-usb-role status` 只读，无需确认；工具不加载 gadget、不导出存储、不自动
恢复 xHCI。切换会拒绝已绑定/已加载 gadget 和仍接在控制器上的 USB 存储。

Release 选择同时匹配发行版和 edition。`-e server|desktop|auto`（Action 输入 `armbian_edition`）检查输入 rootfs 的实际 edition，并写入产物名。

直接使用 `rebuild` 时，默认输入仍为 `build/output/images/*-trunk_*.img`，必须恰有一个；默认输出改为独立的 `build/output/rebuilt/<run-id>/`。可用 `ARMBIAN_INPUT_DIR`、`ARMBIAN_OUTPUT_DIR` 指定绝对路径，输出目录必须为空。Action 会创建独立输入/输出目录，成功路径仍由 `PACKAGED_OUTPUTPATH` 发布。

每个成功产物写入 `artifacts.tsv`（board/release/kernel/edition/file/SHA-256）。跳过全部或部分请求、零产物、缺失或损坏 gzip 均失败；输入 `.img` 不计入产物。`rebuild` 负责压缩，Action 不再重复压缩。

已通过最小归档集测试同名 DTB 覆盖顺序、edition 选择、零产物/全跳过及校验失败路径。
也已使用仓库 October Bookworm server 原始镜像和本机导出的 `6.18.48-ophub`
套件完成完整构建与只读内容核查：FDT、stage1 DTB、内核/config、固件校验表、
更新助手、initramfs hook、edition 和 initrd 均匹配；修正版的 14 项只读
检查全部通过，包含模块索引无失效引用。该镜像未包含新 PHY PM
补丁，尚未启动验收。B1S 精简后会刷新模块索引，并跳过其他板型的 NVRAM 变体生成。

### B1S 引导链与静态检查

本机原厂 eMMC U-Boot 通过 FAT 加载外部盘的 `s905_autoscript`，再导入
`uEnv.txt` 并加载内核/initrd/DTB。型号表的 `MAINLINE_UBOOT` 和
`BOOTLOADER_IMG` 为 `NA`，**无需仅因镜像前 4 MiB 为零而补写裸扇区 u-boot**。
已实机验证的专用恢复盘在该区域同样为零；其启动脚本与此前构建镜像一致。
这不代表镜像可在没有原厂引导器的空白 eMMC 上独立启动。

`rebuild` 在打包前调用 `tools/b1s/check_boot_files.py`，检查脚本和 initrd
的 legacy uImage CRC、脚本源码一致性、未压缩 ARM64 Image、TEXT_OFFSET/
overload 路径、B1S compatible 及根分区 UUID；结果保存为
`/boot/b1s-boot-audit.json`。例如：

```sh
python3 tools/b1s/check_boot_files.py /path/to/boot <rootfs-uuid>
```

此检查不会执行脚本、修改 U-Boot 环境或写裸设备。27 项离线回归通过；
仍需用另一测试介质启动验证，不覆盖唯一已验证的恢复盘。

## 更新策略

新镜像的 `armbian-update` 在安装 tar/deb 前，将**当前 FDT 选择的** B1S DTB 和已选择的 Broadcom 固件保存到 `/ddbr/b1s-assets/<timestamp-pid>/`，校验后在更新完成时恢复，即使内核包带同名 DTB。内核包的替换版另存为 `kernel-provided.dtb`。这样可保留后续现场标定/单项测试的 DTB；应用新板级 DTS 版本需显式重新编译和选择。

若新 initrd 包含对应固件副本，恢复后刷新 initrd 和 uInitrd；没有副本时由驱动从 rootfs 加载。initramfs hook 确保重建时复制选定的板级固件。旧系统若单独换用新版 updater，需同时安装本目录 `rootfs/usr/lib/b1s/` 和 initramfs hook。

此策略不等于证明旧 DTS 适配所有未来内核；跨版本仍需按本板检查存储、网络、USB、温控。真实内核更新测试要在可恢复介质上进行。

## 现场测试与回退

只读快照和无显示接管的 GPU shader/readback 验证：

```sh
python3 tools/b1s/hardware-snapshot.py /path/to/new-evidence-directory
cc -O2 -Wall -Wextra -Werror tools/b1s/gpu-smoke.c -o /path/to/gpu-smoke -lEGL -lGLESv2
python3 tools/b1s/gpu-check.py /path/to/gpu-smoke --output /path/to/new-gpu-evidence
```

GPU 检查需要 Mesa Panfrost、`libegl-dev`、`libgles-dev`；会拒绝软件渲染器，
在 64×64 离屏缓冲中执行 16 次 shader 绘制并逐像素校验；外层检查还会检查
退出 2 秒后的内核日志。实机已能正确绘制，但发现空闲 Runtime PM 错误，
临时保持 active 后 5 次短测无新增错误，详见 `kernel/gpu-runtime-pm.md`。
温控和长时间稳定性仍需分别实测；固定 1080p HDMI 显示/音频已通过单项验收。

先验证 SD/USB 能启动并修改 eMMC 启动分区，再将独立测试 DTB 放进 `/boot/dtb/amlogic/`，只修改 `uEnv.txt` 的 FDT。保留原 DTB 和原 uEnv；失败时从外部系统恢复 FDT。建议先测 `power-key`，再测 `core` 和 `hdmi`，最后合并 `stage1`。

首次电源键测试用 `systemd-inhibit --what=handle-power-key --mode=block --why='B1S test' evtest`，避免尚未记录事件就触发默认关机。HDMI 真实 HPD/EDID 测试需临时去掉强制 `video=HDMI-A-1:1920x1080@60e` 参数。

ADC：本机无实体键，逐键验收不适用。以下采样入口仅供以后加装按键使用：

```sh
python3 tools/b1s/adc-sample.py idle --output idle.csv
python3 tools/b1s/adc-sample.py vol-down --output vol-down.csv
```

原厂 10 位码值 `0/143/271/393/510` 对应约 `0/251.6/476.8/691.5/897.4 mV`，仅作标定参考。只有确认加装实体按键后，才移除 disabled、启用 ADC 驱动并实测阈值。原厂 3.14 模块不能装入本内核。

仍待现场验收：真实遥控关机/唤醒、蓝牙配对/音频、最终镜像启动与真实更新。GPU Runtime PM/调频、HEVC 后端、128 MiB 引导内存保留和 Wi-Fi sleep 超时继续按调查任务处理。
