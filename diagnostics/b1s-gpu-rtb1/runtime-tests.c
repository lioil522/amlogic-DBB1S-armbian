/* Host stubs exercise the extracted, unmodified A/B callback bodies. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define BIT(n) (1U << (n))
#define GPU_PM_RT 0
typedef uint32_t u32;
struct device { void *data; };
struct panfrost_compatible { unsigned int pm_features; };
struct panfrost_device {
    struct { struct device *dev; } base;
    struct panfrost_compatible *comp;
    struct { int pending; } reset;
    struct { u32 shader_present, tiler_present, l2_present; } features;
    unsigned char *iomem;
    void *rstc, *clock, *bus_clock;
    bool b1s_rt_enabled, b1s_diag_enabled;
};
struct platform_device { struct { void *of_node; } dev; };
static struct panfrost_device *current_pf;
static bool board_match, gpu_match, idle;
static int failure, off_mask;
static unsigned char registers[8];
static struct result {
    char ops[2048];
    unsigned int calls;
    int ext_seen, deassert_rc, clock_rc, bus_rc, assert_rc;
    int power_seen, power_rc[3], power_val[3], errors;
    int ret;
} result;

static void event(const char *name) {
    assert(strlen(result.ops) + strlen(name) + 2 < sizeof(result.ops));
    strcat(result.ops, name); strcat(result.ops, ",");
}
static bool of_machine_is_compatible(const char *s) {
    assert(!strcmp(s, "dangbei,b1s")); return board_match;
}
static bool of_device_is_compatible(void *node, const char *s) {
    assert(!strcmp(s, "amlogic,meson-gxm-mali")); return gpu_match;
}
static void *dev_get_drvdata(struct device *d) { return d->data; }
static int atomic_read(int *p) { return *p; }
static int reset_control_deassert(void *p) {
    event("deassert"); return failure == 1 ? -11 : 0;
}
static int reset_control_assert(void *p) {
    event("assert"); off_mask = 0; return failure >= 4 ? -44 : 0;
}
static int clk_enable(void *p) {
    bool bus = p == current_pf->bus_clock;
    event(bus ? "bus-enable" : "clock-enable");
    return failure == (bus ? 3 : 2) || failure == (bus ? 6 : 5) ? (bus ? -33 : -22) : 0;
}
static void clk_disable(void *p) { event(p == current_pf->bus_clock ? "bus-disable" : "clock-disable"); }
static bool panfrost_jm_is_idle(struct panfrost_device *p) { event("idle"); return idle; }
static void panfrost_diag_regs(struct panfrost_device *p, const char *e) { event("regs"); }
static void panfrost_diag2_begin(struct panfrost_device *p) { event("d2-begin"); }
static void panfrost_diag2_end(struct panfrost_device *p, int ret) { event("d2-end"); }
static void panfrost_device_reset(struct panfrost_device *p, bool irq) { assert(irq); event("gpu-reset"); }
static void panfrost_devfreq_resume(struct panfrost_device *p) { event("devfreq-resume"); }
static void panfrost_devfreq_suspend(struct panfrost_device *p) { event("devfreq-suspend"); }
static void panfrost_jm_suspend_irq(struct panfrost_device *p) { event("jm-irq"); }
static void panfrost_mmu_suspend_irq(struct panfrost_device *p) { event("mmu-irq"); }
static void panfrost_gpu_suspend_irq(struct panfrost_device *p) { event("gpu-irq"); }
static void panfrost_diag_log(struct panfrost_device *p, const char *e, const char *fmt, ...) {
    if (!p->b1s_diag_enabled) return;
    va_list args; va_start(args, fmt);
    if (strstr(e, "b1s-rtb1.")) {
        result.ext_seen++;
        result.calls = va_arg(args, unsigned int);
        result.deassert_rc = va_arg(args, int); result.clock_rc = va_arg(args, int);
        result.bus_rc = va_arg(args, int); result.assert_rc = va_arg(args, int);
        assert(strstr(result.ops, "d2-end") || strstr(result.ops, "assert"));
    } else if (!strcmp(e, "power-off.after")) {
        result.power_seen++;
        for (int i=0; i<3; i++) result.power_rc[i] = va_arg(args, int);
        for (int i=0; i<3; i++) result.power_val[i] = va_arg(args, unsigned int);
        assert(!strstr(result.ops, "assert"));
        event("power-off-observed");
    }
    va_end(args);
}
#define SHADER_PWROFF_LO 0
#define TILER_PWROFF_LO 1
#define L2_PWROFF_LO 2
#define SHADER_PWRTRANS_LO 3
#define TILER_PWRTRANS_LO 4
#define L2_PWRTRANS_LO 5
static void gpu_write(struct panfrost_device *p, int reg, u32 value) {
    assert(value == (reg == 0 ? 7U : 1U));
    event(reg == 0 ? "shader-off" : reg == 1 ? "tiler-off" : "l2-off");
}
static int poll_reg(unsigned char *addr, u32 *value, int timeout) {
    int i = addr - registers - 3;
    assert(i >= 0 && i < 3 && timeout == 2000);
    event("poll"); *value = (off_mask & BIT(i)) ? BIT(i) : 0;
    return *value ? -110 : 0;
}
#define readl_relaxed_poll_timeout(addr, val, cond, delay, timeout) poll_reg(addr, &(val), timeout)
#define readl_poll_timeout(addr, val, cond, delay, timeout) poll_reg(addr, &(val), timeout)
static void dev_err(struct device *p, const char *fmt) { result.errors++; event("original-error"); }

#include "extracted-runtime.h"

static struct result execute_case(bool b, bool rt, bool bus, bool diag, int fail, bool is_idle, int mask, bool resume, bool reference) {
    struct device d;
    struct panfrost_compatible comp = {.pm_features = (rt || (reference && b)) ? BIT(GPU_PM_RT) : 0};
    struct panfrost_device pf = {.base.dev=&d, .comp=&comp, .clock=(void *)1,
        .bus_clock=bus ? (void *)2 : NULL, .b1s_rt_enabled=b, .b1s_diag_enabled=diag,
        .iomem=registers, .features={7, 1, 1}};
    d.data=&pf; current_pf=&pf; failure=fail; idle=is_idle; off_mask=mask;
    memset(&result, 0, sizeof(result));
    result.ret = reference ? (resume ? a_runtime_resume(&d) : a_runtime_suspend(&d)) :
                             (resume ? panfrost_device_runtime_resume(&d) : panfrost_device_runtime_suspend(&d));
    return result;
}

int main(void) {
    unsigned int cases = 0;
    for (int board=0; board<2; board++) for (int gpu=0; gpu<2; gpu++) for (int diag=0; diag<2; diag++) {
        struct panfrost_device pf = {.b1s_diag_enabled=diag};
        struct platform_device pdev = {};
        board_match=board; gpu_match=gpu;
        extracted_probe_gate(&pf, &pdev);
        assert(pf.b1s_rt_enabled == (board && gpu));
    }
    for (int b=0; b<2; b++) for (int rt=0; rt<2; rt++) for (int bus=0; bus<2; bus++)
    for (int diag=0; diag<2; diag++) for (int fail=0; fail<7; fail++) for (int is_idle=0; is_idle<2; is_idle++)
    for (int mask=0; mask<8; mask++) for (int resume=0; resume<2; resume++) {
        struct result x = execute_case(b,rt,bus,diag,fail,is_idle,mask,resume,false);
        struct result a = execute_case(b,rt,bus,diag,fail,is_idle,mask,resume,true);
        assert(x.ret == a.ret && !strcmp(x.ops, a.ops));
        assert(x.errors == a.errors && x.power_seen == a.power_seen);
        assert(!memcmp(x.power_rc, a.power_rc, sizeof(x.power_rc)));
        assert(!memcmp(x.power_val, a.power_val, sizeof(x.power_val)));
        assert(x.ext_seen == (b && diag && (resume || is_idle)));
        if (!resume && is_idle && diag) {
            for (int i=0; i<3; i++) {
                assert(x.power_rc[i] == ((mask & BIT(i)) ? -110 : 0));
                assert(x.power_val[i] == ((mask & BIT(i)) ? (int)BIT(i) : 0));
            }
        }
        if (x.ext_seen) {
            unsigned int calls;
            if (!resume) calls = BIT(4) | BIT(5) | (bus ? BIT(3) : 0);
            else if (fail == 1) calls = BIT(0);
            else if (fail == 2 || fail == 5) calls = BIT(0) | BIT(1) | BIT(5);
            else if ((fail == 3 || fail == 6) && bus) calls = BIT(0) | BIT(1) | BIT(2) | BIT(4) | BIT(5);
            else calls = BIT(0) | BIT(1) | (bus ? BIT(2) : 0);
            assert(x.calls == calls);
            assert(x.deassert_rc == ((calls & BIT(0)) && fail==1 ? -11 : 0));
            assert(x.clock_rc == ((calls & BIT(1)) && (fail==2 || fail==5) ? -22 : 0));
            assert(x.bus_rc == ((calls & BIT(2)) && (fail==3 || fail==6) ? -33 : 0));
            assert(x.assert_rc == ((calls & BIT(5)) && fail>=4 ? -44 : 0));
        }
        cases++;
    }
    printf("PASS: 8 board/GPU/diag gates; %u A/B callback cases, including original pre-reset power-off failures\n", cases);
}
