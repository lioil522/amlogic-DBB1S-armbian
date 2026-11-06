# Release 快照配置与固定源码的差异

`inputs/config.diff` 为完整 olddefconfig 差异，不只包含用户指定的后缀。

- 有意变更：LOCALVERSION 为 `-b1s-gpu-diag1`；关闭 LOCALVERSION_AUTO（原配置已关闭）；完整 DWARF5 改为无损 zlib 压缩。未启用 DEBUG_INFO_REDUCED/SPLIT，内核及模块 BTF 保留。
- 工具能力变化：GCC 15.3.1→本地 12.2.0、binutils 2.45.1→2.40、pahole 1.31→1.24；相关 asm-goto、counted-by、memset/函数对齐能力及 RELR 由 Kconfig 重新计算。GCC plugin 开发头文件未安装，原配置仅开启插件框架而无实际插件，本地去除不可用框架。未安装/升级编译器或依赖以掩盖差异。
- **配置不与该固定源码完全对应**：当前 Release config 包含源码中没有的 PCS_XPCS_PLATFORM/ROCKCHIP、RTW88_8723BS、AMLOGIC_DMA 项，olddefconfig 去除它们。其中 ROCKCHIP 项原为 y，不是全部为禁用项。DRM_AUX_BRIDGE 在该源码为 tristate，依其选择者重算为 m（Release 为 y）。这些不能归因于工具链，进一步限制 Release/固定源码的对应性；不临时换提交或给旧源码加入未审查驱动。
- 对 B1S 所需 PM/OPP/devfreq/simple_ondemand、Panfrost、thermal/thermal_of/devfreq_thermal、USB DWC2/DWC3 dual-role、gadget/role-switch/GXL PHY、DWARF5/BTF/BTF_MODULES 逐项确认，启用值与输入一致。
- 本构建是固定 ophub 提交上的独立诊断内核，非发布二进制的字节级重现，也不是只差 printk 的严格二进制 A/B 对照。实机是否启动/故障复现尚未验证。

## 构建保护

- `compile.py` 只接受 panfrost 或 full（Image/modules）目标，不接受 install、modules_install、dtbs_install。
- 默认 `-j2`，最大 2；nice 10；每 10 秒检查空间/内存。磁盘可用小于 2GiB、或 MemAvailable 小于 128MiB 且 SwapFree 小于 256MiB，终止该构建进程组并保留所有已产生文件；不删除旧证据、不改全局服务/swap。
- 输入、修改源码、完整补丁、配置及关键工具执行文件哈希在 `INPUT-SHA256.json` 中冻结，每次编译前核验。完整补丁（包括新 header）已对固定 HEAD 的临时 index 做 apply --cached --check。
- 首次 Panfrost 对象编译触及工具 120 秒执行期限，由 SIGTERM 正常停止并留有 JSON 状态；不是驱动错误或资源保护触发。之后后台续编。
