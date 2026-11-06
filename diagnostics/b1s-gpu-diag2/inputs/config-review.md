# diag2配置审查

参考配置与已认证diag1云端配置相比仅LOCALVERSION改为`-b1s-gpu-diag2`。完整DWARF5使用无损zlib压缩，保留BTF/BTF_MODULES，不启用DEBUG_INFO_REDUCED/SPLIT。

沿用diag1的原Release快照作为olddefconfig起点：快照与固定源码并非完全对应，部分源码未知驱动项会消失，DRM_AUX_BRIDGE由y重算为m；这些不是工具链差异，也不临时换源码或添加未审查驱动。具体diff在云端evidence/release.config.diff中记录。快照不是已证明的历史镜像输入。

固定Debian12容器/GCC12交叉编译、binutils及pahole，apt仓库不是时间快照，实际版本和能力变化记录并对照local-resolved.config核查。GPU/PM/OPP/devfreq/thermal及USB双角色、DWARF/BTF和CC_CAN_LINK保持参考启用值，其他非白名单配置差异即失败。不是历史镜像或本地对象的逐字节二进制重现。

仅云端完整构建，沿用已审计diag1的资源保护、Image/模块/DWARF/BTF核查和失败证据保留，不安装、改FDT/PM策略、关闭服务或运行GPU负载。
