# RTB1配置审查

`local-resolved.config`与认证diag2的`a-diag2.config`逐字节仅将LOCALVERSION从`-b1s-gpu-diag2`改为`-b1s-gpu-rtb1`。

沿用A版完整DWARF5/zlib压缩、BTF/BTF_MODULES，保持DEBUG_INFO_REDUCED/SPLIT关闭，保留CC_CAN_LINK和USB gadget/dual-role能力。原始发布配置/资产来源继续冻结。

olddefconfig仅允许原diag2已审查工具链相关差异并逐项记录；任何非工具链功能变化或必需项变化立即拒绝。B行为由源码中B1S+GXM双限制启用，不添加通用PM配置、板级DT或旧pm1 PHY行为修改。
