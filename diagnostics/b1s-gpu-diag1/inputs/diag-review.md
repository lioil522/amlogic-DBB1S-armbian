# diag1 静态审查

- 仅 `dangbei,b1s` 根节点且 GPU 为 `amlogic,meson-gxm-mali` 时启用，模块参数 `b1s_diag` 默认 true、权限 0400。未改变匹配表或 FDT。
- 每设备/probe 最多 256 事件（快照事件两行）及一次预算提示。计数原子饱和在 257，不会因长期递增溢出重新开放；原错误/告警不受预算影响。首次休眠之前的 probe/reset 也消耗预算；预算耗尽后的实验不再具有完整诊断覆盖。
- 无新增 PM 引用/策略调用、寄存器写入、延时、调频/供电操作。原写寄存器、PM 变更/引用、错误/告警调用序列逐项比较。soft reset 返回值仅记录，仍按原逻辑继续。
- MMIO 快照仅在 probe device_init 成功且 runtime PM 尚未 enable、runtime resume 外部资源恢复后、soft reset 前/后、power_on 之后、MMU reset 之后、runtime suspend 关闭 IRQ/内部电源/外部时钟之前。与原驱动同期 MMIO 可访问边界一致；不额外保证失效硬件读操作正常。
- runtime suspend 结束、内部 power_off 之后、resume 入口/清理、reset worker、AS wait、ctx release 使用状态/原轮询值，无新增 MMIO。IRQ 日志复用原中断状态/地址，不新增 IRQ 读寄存器。未增加锁或在 printk 内申请 PM 引用。
- 各快照为非原子、低 32 位 ready/trans；B1S Mali-T820 的核位在低 32 位。地址高低读可能和 IRQ/reset 活动交错，事件 seq 不是严格日志打印顺序。
- 正常输出字符串（包括事件标签）避开 gpu-check 的 `fault|timeout|stuck|error`，fs/addr 以十六进制输出；AS caller 符号仅在原 AS_ACTIVE 卡住错误后输出。
- 额外 printk/只读 MMIO 可能改变竞态时序；诊断版不是修复版。256 事件不是时间覆盖保证。
- SFE `fast-classifier/Makefile` 单独修正 `-I$(obj)/../shortcut-fe` 为 `-I$(src)/../shortcut-fe`，仅支持隔离 O= 构建找到源码头文件，不合入旧 PHY/板级行为改动。
