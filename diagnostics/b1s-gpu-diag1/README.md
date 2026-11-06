# B1S GPU diag1 独立 Actions 构建

仅诊断分支 `diag/b1s-gpu-diag1` 的相关路径 push 触发。新工作流不需要进入默认分支，无 workflow_dispatch 注册前提；以后可在 Actions 对该 run 选择 Re-run。工作流只有 contents:read，不创建或覆盖 Release，不部署或运行 GPU 测试。

## 固定输入

- 源码：`https://github.com/ophub/linux-6.18.y.git`，提交 `cea7acd4c5be4710f197426f017b56006e01367a`。
- 后缀：`6.18.60-b1s-gpu-diag1`，LOCALVERSION_AUTO 关闭。
- `inputs/source-changes.patch`：与已完成本地 Panfrost 对象验证的完整补丁逐字节相同，包含新 header 和独立 SFE O= 头文件源路径修正；没有旧 pm1 PHY/板级行为补丁。
- `inputs/release.config`：当前 6.18.60 Release 快照配置；下载包/内包校验已经通过，保存包的摘要/资产元数据。该资产于镜像构建后更新，**不是已证明的历史镜像输入**。
- `inputs/local-resolved.config`：本地 GCC12 olddefconfig 的对照，不直接冒充云端配置。云端重新解析并保存全部差异，仅允许已识别的工具能力变化；PM/GPU/cooling/USB/DWARF/BTF值保持一致。
- `inputs/SHA256SUMS`：入仓输入摘要。
- `inputs/source-files.json`：八个修改/新增源码文件的精确SHA-256与权限。校验全部diff路径/模式/补丁载荷及实际文件内容，仅忽略Git随对象数量改变的index对象ID缩写；观察到的云端补丁另存证据，不修改原冻结补丁。

## 边界

Panfrost 仅在 B1S/GXM 上启用观测。保留 PM 引用/策略、50ms autosuspend、频率、供电、原 MMIO 写顺序和故障日志。事件计数原子饱和，每设备/probe 256 事件及一次提示；快照非原子，额外日志/MMIO会影响竞态。它不是修复补丁。详见 `inputs/diag-review.md`。

云端使用固定 digest 的 Debian12 amd64 容器和 GCC12 ARM64 交叉编译，记录安装包版本、工具摘要、配置、源码补丁、Actions 提交/运行标识及资源。apt 包仓库不是时间快照，记录实际解析版本，不声称字节级重现。源码/Kconfig不与Release配置完全对应及DRM_AUX_BRIDGE重算已记录在 `inputs/config-review.md`。

并发按 runner CPU/内存限制，最大8；每10秒检查磁盘/内存，空间不足停止本轮进程组，不删除旧证据或扩大清理范围。先编译 Panfrost 对象，再完整 Image/modules，最后核查 ARM64 Image、内核版本、全部模块 vermagic、DWARF/BTF及诊断字符串并打包。

## 产物

成功时 Actions artifacts 包含 Image、System.map、Module.symvers、resolved.config、全部模块归档、完整调试 vmlinux 归档、逐模块哈希和审计报告；故障时仍上传可用证据。每个归档/主产物均有 SHA-256；不打包基础 rootfs 或替换正式 DTB。

模块归档布局为 `lib/modules/6.18.60-b1s-gpu-diag1/kernel/...`，保留模块调试信息和 BTF、builtin/order 元数据；没有在构建机运行系统安装或生成目标系统 initrd。**不是可直接启动的镜像，也不提供自动安装脚本。** 实机安装、FDT/启动更改、重启和 GPU 诊断负载均须另行授权，并先确认 USB 根盘及可恢复引导条件。
