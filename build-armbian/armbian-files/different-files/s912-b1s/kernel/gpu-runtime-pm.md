# Panfrost Runtime PM 实测（2026-11-02）

环境：原始 `6.18.48-ophub` 和原 DTB，Mesa 22.3.6，Mali-T820 MP3。

| 条件 | shader/readback | 新增内核错误 |
|---|---|---|
| 默认 `power/control=auto` | 16 次绘制、65536 像素通过 | shareability/bus fault、AS_ACTIVE stuck、shader/tiler/L2 掉电超时 |
| 临时 `power/control=on` | 连续 5 次进程，各 16 次绘制，全通过 | pin、绘制和进程间空闲期间无新增日志 |
| 恢复 `auto` 后 2 秒 | 未追加绘制 | 再次出现 shader/tiler/L2 power transition timeout |

该对比支持 Runtime PM 空闲掉电路径是当前稳定性问题的重要触发条件。
首次 shareability/bus fault 是否同因仍需确认。像素读回通过不能单独作为
GPU 验收成功；使用 `tools/b1s/gpu-check.py` 同时检查退出后的内核日志。

现场原来的 `power/control=auto` 已恢复。临时复测方法：

```sh
control=/sys/devices/platform/soc/d0000000.apb/d00c0000.gpu/power/control
original=$(cat "$control")
trap 'printf "%s\n" "$original" > "$control"' EXIT
echo on > "$control"
python3 tools/b1s/gpu-check.py /path/to/b1s-gpu-smoke --output /path/to/new-gpu-check
```

保持 active 会增加空闲功耗；持续稳定性、实际供电、温控联动和冷热启动
仍需验收。因此该实验未直接加入默认镜像的启动服务。

相关参考：Armbian
[PR #10797](https://github.com/armbian/build/pull/10797)（同为 GXM/T820 的
Magicsee C400 Plus）包含可选的 `linux,pin-runtime-pm-probe` 补丁。
它不是 Linux 主线通用修复，也不能据此假定 B1S 的 GPU 供电为 1.0 V；
需要结合本板原厂 DTS/电源电路确认后再选择内核级方案。
