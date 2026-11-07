# B1S GPU rtb1：独立B版完整构建

已授权独立`diag/b1s-gpu-rtb1`分支提交/推送及GitHub Actions完整构建。工作流仅本分支相关路径push触发、contents:read，产物保存至Actions artifacts。

## 冻结输入

- 固定ophub/Linux提交`cea7acd4c5be4710f197426f017b56006e01367a`，版本`6.18.60-b1s-gpu-rtb1`。
- A版是已认证diag2（项目提交`a36775c9a9c88f82878ddc2e9d4c9f3d3f24ca44`）。B相对A仅三个Panfrost文件和CONFIG_LOCALVERSION变化。
- `inputs/source-changes.patch`是本地完整补丁的Git序列化副本；原始增量/完整补丁与8文件SHA/权限清单同时保留。校验完整补丁内容，仅归一化Git index对象ID缩写。
- `inputs/local-resolved.config`保持A的PM/OPP/auto/50ms相关配置、USB device/gadget能力、完整压缩DWARF5/BTF；`release.config`及原发布来源继续保留。
- 本地Panfrost对象2917216字节，SHA`3d55853090873d76bc07088eb315ab425f3fd505256a52d9fbec6bf18a6f1de0`，只是先行对象验证，不是可安装内核。

## B行为和观测

probe只在`dangbei,b1s`根节点且GPU为`amlogic,meson-gxm-mali`时设置每设备B位，扩展原4处GPU_PM_RT判断。共享amlogic_data不改，pm_features仍可为0；原b1s_diag开关只控制观测，关闭它不是A回退。

挂起保留原idle/IRQ/内部PWROFF/轮询及错误记录，然后按原GPU_PM_RT顺序disable时钟/assert reset数组；恢复先deassert/enable，再执行原软复位/quirk/PWRON/MMU/JM/devfreq。所有原内部写入顺序、PM引用、轮询条件/超时和错误处理保持；13点diag2与首IRQ观测原样保留。

每回调局部RAM记录外部调用位图及原返回值，尾部输出并共用256事件预算。calls位0/1/2/3/4/5分别是reset-deassert/clock-enable/bus-enable/bus-disable/clock-disable/reset-assert；无bus的B1S正常resume=03、suspend=30。未调用的rc=0不表示成功，clock-disable为void。reset返回值属于数组API，不是两条线的物理测量。

原suspend及PWROFF失败处理不改：**必须检查外部assert之前的原三路掉电rc/TRANS和错误，避免reset清零掩盖失败**。外部reset/clock是组合变量；单次B成功不能单独归因某条reset或clock。

## 构建与审计

继承认证diag2的固定Debian12 amd64容器/GCC12交叉工具链、资源保护和完整构建审计。先跑离线契约、固定源码/配置校验、真实diag2 helper/160万次预算替身，以及3584组实际A/B回调替身（含双重API失败和reset前掉电失败），再编译Panfrost、完整Image/modules。

最终核验ARM64 Image文件/内存边界和从vmlinux重生成SHA、完整模块集合/vermagic、B版与diag2双载荷、vmlinux/Panfrost压缩DWARF及BTF。产物包含Image、System.map、Module.symvers、resolved.config、全部原调试模块、调试vmlinux、逐模块SHA及审计报告。模块路径为`lib/modules/6.18.60-b1s-gpu-rtb1/kernel/...`，builtin/order元数据保留。

失败保留证据；编译成功但审计失败的输出标记为UNAUDITED。旧diag1/diag2目录与工作流保留。完整构建及静态审计成功也不等于实机GPU通过；本工作流不安装、不重启、不运行GPU负载，不修改main/Release或测试USB。后续安装/启动/真实A-B-A及多runtime周期验收另行授权。
