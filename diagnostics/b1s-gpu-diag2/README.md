# B1S GPU diag2 独立 Actions 完整构建

用户已授权独立`diag/b1s-gpu-diag2`分支提交/推送及云端完整构建。仅该分支相关路径push触发，contents:read，仅上传Actions artifacts，不改main/Release，不部署、安装、重启或运行GPU负载。保留diag1目录及工作流。

## 固定输入与来源

- ophub/linux-6.18.y提交`cea7acd4c5be4710f197426f017b56006e01367a`，后缀`6.18.60-b1s-gpu-diag2`。
- `inputs/source-changes.patch`是本地已验证完整diag2补丁的Git序列化副本；原统一diff保留为`local-upstream-to-diag2.patch`及`local-diag1-to-diag2.patch`。序列化格式不同但重放后八文件SHA/权限完全一致，云端严格核验全部载荷/模式和实际文件摘要，仅忽略Git index对象ID缩写长度。
- `inputs/source-files.json`为本地冻结源码清单，`local-resolved.config`为已认证diag1云端配置仅改LOCALVERSION的对照。云端沿用diag1的Release快照重新olddefconfig，GPU/PM/thermal/USB双角色/DWARF5/BTF/CC_CAN_LINK必须不变；工具能力差异记录且只按明确白名单接受。
- Release快照及下载元数据来自原diag1已认证材料，不等于历史发布镜像的已证明输入。Debian12 amd64容器digest与diag1相同；apt包仓库非时间快照，记录实际包版本/工具摘要，不声称逐字节二进制重现。
- 输入逐项摘要见`inputs/SHA256SUMS`；本地源码契约、负向/替身及最终对象审计材料一并保留。

## 诊断边界

仅B1S/GXM启用，b1s_diag默认true/0400，每probe256事件/饱和257；原PM引用、auto/50ms、IRQ/寄存器写顺序、轮询条件/超时、供电/频率/FDT及故障检查保持。

新观测仅首次resume的owner写固定RAM：普通/vendor quirk前后及L2/shader/tiler PWRON前/写后/原轮询结束，共13点、最多26次新RAWSTAT/FAULT_STATUS relaxed读取。三个polled点复用原目标/最终值/返回值，首个error IRQ复用原state/fs/address，以release/acquire发布，状态1不读半写数据。probe/reset-worker/后续resume不新增此类读取。保留原AS/reset/掉电观测，不混入新AS/LOCK打点。

批量打印在硬件恢复步骤结束后、回调返回前，仍延迟真实返回，读操作也会扰动竞态。`ns=begin/end`是采样区间，不是整个回调区间；不得按延迟printk时间/seq强行推定硬件顺序。诊断版不是修复版。

## 验证、产物及安全范围

先离线构建契约，再固定源码/补丁/配置及真实helper用户态替身测试、Panfrost对象、完整Image/modules；不在runner执行真实GPU测试。最后核查ARM64 Image文件与有效内存边界、从vmlinux objcopy重生成SHA、模块集合/vermagic、Panfrost diag2载荷、vmlinux/Panfrost压缩DWARF和BTF。

完整产物含Image、System.map、Module.symvers、resolved.config、全部调试模块归档、调试vmlinux归档、逐模块摘要和audit报告。模块布局为`lib/modules/6.18.60-b1s-gpu-diag2/kernel/...`，保留builtin/order元数据。失败保留证据，编译成功但审计失败的原产物明确标为UNAUDITED，不能安装。

构建初始需12GiB以上可用磁盘，并发按CPU/内存限制最大8，每10秒监测资源、触底终止本轮进程组；不广泛清理runner或删除旧证据。成功构建不等于本地下载核验、USB启动或GPU验收。实机安装/启动及新采证需另行授权，不生成目标initrd、不替换正式DTB/rootfs，不提供自动安装脚本。
