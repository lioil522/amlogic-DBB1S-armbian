# B1S 内核候选构建

## 已确认的 PHY PM 回归

参考源码 `ophub/linux-6.18.y@e0497a975383cedc8b8d8a7c9e84eac2e2238a6e` 的
`drivers/phy/phy-core.c:phy_pm_runtime_put()` 缺少 `pm_runtime_enabled()` 检查。
Meson GXL PHY 驱动没有启用 Runtime PM，`phy_init()` 中 get 返回
`-ENOTSUPP`，put 却仍递减计数，正好对应本机三个 PHY 的 underflow。

本机 `6.18.48-ophub` 启动 Image 反汇编也确认：`phy_pm_runtime_put`
（System.map 地址 `ffff800080a34d70`）仅检查空指针，随后直接调用
`__pm_runtime_idle`，缺少 PM-enabled 分支。三个 PHY 的 sysfs
`power/runtime_status` 为 `unsupported`。

采用已有上游修复：
[0287c960b15f27498d85cadf584bb59301ea2382](https://github.com/torvalds/linux/commit/0287c960b15f27498d85cadf584bb59301ea2382)，
保留作者和提交说明。补丁已在精确参考文件上无 fuzz 应用，并使用本机
6.18.48 headers 完成对象编译。该检查使用 GCC 12，将 headers 中 GCC 15
专用的 `-fmin-function-alignment=4` 替换为 `-falign-functions=4`；
它只验证编译，生成的对象未安装。**修复后的完整内核尚未启动验证。**

## DWC2 reset timeout 的调查状态

- GXL/GXM 初始化先将 OTG PHY 设为 device；但 `usb_post_init()` 在
  `of_platform_populate()` 前又切回默认 host。启动 Image 中回调位于
  `ffff800080e20894`，populate 调用位于 `ffff800080e208b0`，与参考源码一致。
- 当前 PHY1 R0=`0x00252060`（DM/DP pulldown 均置位），glue R0=`0x0000006a`
  （U2D_ACT 未置位），R5=`0x0000ff30`，sysfs role=`host`。
  这是故障后的快照，不能直接证明发生 reset timeout 时的寄存器状态。
- DWC2 的 generic PHY 初始化不会自行调用 `phy_set_mode()`；本板与参考
  `meson-gxl.dtsi` 一样有 `phys = <&usb2_phy1>`，但缺少 `phy-names`。
  DWC2 按 `usb2-phy` 名称查找，参考 PHY 核心的查找失败路径会返回
  `-ENODEV`，控制器继续无 generic PHY 的初始化；父级 glue 仍管理该 PHY。
- 还需核对 GXM drvdata 的 `.num_clks = ARRAY_SIZE(meson_g12a_clocks)`：
  虽然绑定指定 `usb_ctrl`、`ddr`，参考实现只获取一个时钟。当前
  `gxbb_usb1_ddr_bridge` 无消费者、enable/prepare count=0，但硬件 gate
  显示 Y；这不能作为它导致 reset timeout 的证据。
- 直接将 post-init 移到 populate 后不能保证 deferred/module probe 的顺序；
  也必须考虑 GXL 空端口 device→host 切换限制及恢复路径。尚无足够证据合入
  时序修复。保留最终 OTG/gadget 需求，GPIOAO_5 VBUS 仍是独立接线候选。
- 已核对源码和运行 Image 反汇编：首次 `dwc2_core_reset()` 位于
  `dwc2_get_hwparams()`、`dwc2_force_dr_mode()`、DRD/gadget 初始化及 UDC
  注册之前。当前错误是 CSFTRST 清零等待失败（约 10 ms 加 MMIO 开销），
  不是后续 AHB idle 等待失败；已有 `dr_mode = "peripheral"` 不能绕过它。
- 单独增加 `phy-names = "usb2-phy"` 也不会显式切换到 device 模式。
  DWC2 只调用 `phy_init()/phy_power_on()`，共享 PHY 若已由父 glue 初始化，
  核心计数会跳过 provider 的重复 init/power-on 回调。仍需在可恢复启动中
  捕获首次 reset 前的 glue/PHY/时钟状态，不能将这个命名修改称为修复。

## 固定输入的构建入口

`config-6.18.48-ophub` 是当前机器完整配置，哈希与来源在 `reference.json`。
准备脚本会固定源码提交，在新 worktree 中应用 PM/SFE 构建补丁及 stage1 DTS，
保存输入/最终配置、配置差异、源码补丁、工具版本/哈希和包清单，生成
独立 release `6.18.48-b1s-pm1`。ADC 尚未标定，因此候选保持 ADC driver 关闭。

依赖：完整 Linux Git checkout、aarch64 GCC/binutils（或交叉工具链）、
make、flex、bison、bc、OpenSSL/libssl-dev、libelf-dev、pahole、DTC、Python 3；
制作 deb 另需 dpkg-dev 等内核打包依赖。

```sh
# 在网络和构建空间充足的机器获取完整源码
git clone --no-checkout https://github.com/ophub/linux-6.18.y.git linux-b1s
git -C linux-b1s checkout --detach e0497a975383cedc8b8d8a7c9e84eac2e2238a6e
board=build-armbian/armbian-files/different-files/s912-b1s
bash "$board/prepare-kernel.sh" /path/to/linux-b1s \
    "$board/kernel/config-6.18.48-ophub" /path/to/new-b1s-build
# x86 构建机需在上一命令前指定 CROSS_COMPILE=aarch64-linux-gnu-

cat /path/to/new-b1s-build/inputs/config.diff
JOBS=2 bash /path/to/new-b1s-build/compile.sh Image modules dtbs
JOBS=2 bash /path/to/new-b1s-build/compile.sh bindeb-pkg
```

`compile.sh` 校验保存的输入、配置和主要编译工具，不运行 install 或修改
启动选择。新工具链经 olddefconfig 会改变能力相关配置，差异会完整保存；
记录工具链并不表示已获得原构建容器或能够逐字节复现运行内核。
本机已用完整固定提交源码实跑 prepare/olddefconfig，通过源码与参考文件
核对和 stage1/USB Kconfig 检查。GCC 12/binutils 2.40/pahole 1.24 相比原
工具链的能力变化已记录在 `inputs/config.diff`（包括 RELR、GCC plugins
及 function-alignment capability）。完整源码下 PHY 对象和板级 DTB 已编译
通过，DTB 与 stage1 语义一致，不需要 headers 验证时的 flags workaround。
完整 Image 已编译成功；模块阶段发现 ophub SFE 的独立输出目录构建错误。
`0002-sfe-fast-classifier-use-source-include-path.patch` 将 sibling headers 路径从
`$(obj)/../shortcut-fe` 改为 `$(src)/../shortcut-fe`。本版本 Kbuild 的 `src`
已包含 `srcroot`，不再额外拼接 `srctree`。该补丁仅修正本地构建路径，不是
主线上游补丁，也未禁用 SFE。精确源码上无 fuzz 应用和 fast-classifier
对象编译已通过；新增离线回归验证源码/输出隔离的 include 查找行为。
截至 2026-11-04，完整 Image、2956 个模块、DTB 和三个 deb 均已构建完成，
套件内容核查及隔离 headers 外部模块编译通过。候选尚未安装/启动；
验证恢复介质后再进行启动和内核更新验收。

本机模块最终链接曾触及磁盘保护阈值（剩余约 2 GiB），没有新的编译错误。
续建前对已完成模块进行 `objcopy --compress-debug-sections=zlib` 无损
调试段压缩，逐段验证解压后的数据、代码、符号和 BTF 均不变并保留时间戳。
这属于有记录的产物后处理，不改变 Kconfig，也没有去掉调试信息；
压缩工具及逐文件哈希记录纳入构建 inputs/provenance。
首次压缩释放约 698 MiB，续建后再次触及空间保护。已核对并清理可重建的
首轮链接临时文件 `.tmp_vmlinux1`，保留最终 Image、vmlinux、源码及备份；
第二次压缩再释放约 204 MiB，随后两线程增量构建和打包全部成功。

在空间受限的机器可仅跳过单独的 debug 符号 deb，不改变内核配置中的
调试信息/BTF，也不省略 image/modules/headers：

```sh
DEB_BUILD_PROFILES=pkg.linux-upstream.nokerneldbg KDEB_SOURCENAME=linux-upstream \
    JOBS=2 bash /path/to/new-b1s-build/compile.sh bindeb-pkg
```

本机续建采用该 profile，并限制 deb 压缩为 gzip、两线程；打包环境记录在
构建结果 JSON 中。候选 Image 的编译成功不代表已有完整可安装的内核套件。

本机已完成套件的 release 为 `6.18.48-b1s-pm1`，deb Version 为
`6.18.48-ge0497a975383-1`，包含 image（含模块/DTB）、headers 和 libc-dev。
ARM64 的默认 `image_name` 为 `Image.gz`，包内 `/boot/vmlinuz-*` 是 gzip，
不能直接与 raw Image 比哈希。已分别确认其与 Image.gz 逐字节一致，
解压后与 raw Image 一致；模块集合、索引和代表模块 vermagic 也通过核查。
套件清单为本机 `artifacts/kernel-suite-pm1-manifest.json`，包目录中的
`PACKAGE-SHA256SUMS` 可验证三个包。专用 initrd、Armbian 引导格式交接和
实机更新尚未验收，不应直接无人值守安装。此前验证的完整 Armbian 镜像
仍使用原 `6.18.48-ophub`，**不含此 PHY PM 修复**。

受限网络可以给 Git 命令单独设置 `-c http.proxy=<代理地址>` 和
`-c http.version=HTTP/1.1`。已有 partial clone 若 checkout 补取大量文件失败，
可先 `git fetch --refetch --no-filter --depth=1 origin <固定提交>` 再 checkout；
本次通过该方式完成下载，构建前核对固定 HEAD 和工作区洁净状态。
