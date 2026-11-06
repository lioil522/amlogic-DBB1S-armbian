#!/usr/bin/env python3
"""Userspace tests of actual helper code with RAM/atomic/MMIO stubs, not a GPU test."""
import json
import os
import re
import subprocess
from pathlib import Path

WORK = Path(os.environ['B1S_DIAG_WORK']).resolve()
PF = WORK / 'source/drivers/gpu/drm/panfrost'


def main():
    (WORK / 'validation').mkdir(exist_ok=True)
    header = (PF / 'panfrost_diag.h').read_text()
    fields = re.findall(r'/\* B1S_DIAG2_BEGIN \*/(.*?)/\* B1S_DIAG2_END \*/',
                        (PF / 'panfrost_device.h').read_text(), re.S)
    helpers = re.findall(r'/\* B1S_DIAG2_BEGIN \*/(.*?)/\* B1S_DIAG2_END \*/', header, re.S)
    helpers = '\n'.join(block for block in helpers if 'static inline' in block)
    budget = re.search(r'static inline int panfrost_diag_take\(.*?\n\}', header, re.S).group(0)
    limit = re.search(r'#define PANFROST_B1S_DIAG_LIMIT \d+', header).group(0)
    program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <pthread.h>
#include <string.h>
typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { atomic_int counter; } atomic_t;
struct task_struct { int id; };
static _Thread_local struct task_struct *current;
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define atomic_read(x) atomic_load(&(x)->counter)
#define atomic_set(x,v) atomic_store(&(x)->counter,v)
#define atomic_read_acquire(x) atomic_load_explicit(&(x)->counter,memory_order_acquire)
#define atomic_set_release(x,v) atomic_store_explicit(&(x)->counter,v,memory_order_release)
static int atomic_cmpxchg(atomic_t *v, int old, int new) {
    atomic_compare_exchange_strong(&v->counter,&old,new); return old;
}
static int atomic_fetch_add_unless(atomic_t *v,int a,int unless) {
    int old=atomic_load(&v->counter);
    while(old!=unless && !atomic_compare_exchange_weak(&v->counter,&old,old+a)) {}
    return old;
}
static u64 clock_ns;
static u64 ktime_get_mono_fast_ns(void) { return ++clock_ns; }
static unsigned reads, notices, logs, irq_logs;
#define GPU_INT_RAWSTAT 0
#define GPU_FAULT_STATUS 4
static u32 readl_relaxed(void *p) { ++reads; return *(u32 *)p; }
#define dev_info(dev,...) ((void)(dev),++notices)
''' + fields[0] + r'''
struct panfrost_device {
    bool b1s_diag_enabled;
    atomic_t b1s_diag_seq;
    struct { void *dev; } base;
    void *iomem;
    struct { void *vendor_quirk; } *comp;
''' + fields[1] + '\n};\n' + limit + '\n' + budget + r'''
static void fake_log(struct panfrost_device *pfdev,const char *event,const char *fmt,...) {
    if(!panfrost_diag_take(pfdev)) return;
    char buffer[512]; va_list ap;
    va_start(ap,fmt); vsnprintf(buffer,sizeof(buffer),fmt,ap); va_end(ap);
    ++logs; if(!strcmp(event,"boundary.irq")) ++irq_logs;
}
#define panfrost_diag_log(pfdev,event,fmt,...) fake_log(pfdev,event,fmt,##__VA_ARGS__)
''' + helpers + r'''
static struct task_struct a={1},b={2};
static u32 mmio[2]={0x81,0x008403af};
static struct { void *vendor_quirk; } comp={0};
static struct panfrost_device dev;
static void reset_fixture(bool enabled) {
    memset(&dev,0,sizeof(dev));
    dev.b1s_diag_enabled=enabled; dev.iomem=mmio;
    dev.comp=(void *)&comp; current=&a;
    reads=logs=irq_logs=notices=0; clock_ns=100;
    panfrost_diag2_init(&dev);
}
static void *record_irq(void *unused) {
    (void)unused; current=&b;
    panfrost_diag2_irq(&dev,0x81,0x008403af,0xfbc77d06fad5ea85ULL);
    return NULL;
}
int main(void) {
    reset_fixture(false);
    panfrost_diag2_begin(&dev);
    panfrost_diag2_mark(&dev,B1S_D2_GENERIC_BEFORE);
    panfrost_diag2_irq(&dev,1,2,3); panfrost_diag2_end(&dev,0);
    assert(!reads && !logs && atomic_read(&dev.b1s_diag2.state)==0);

    reset_fixture(true);
    panfrost_diag2_mark(&dev,B1S_D2_GENERIC_BEFORE); /* probe/reset has no owner */
    assert(!reads);
    panfrost_diag2_begin(&dev);
    current=&b; panfrost_diag2_mark(&dev,B1S_D2_GENERIC_BEFORE);
    panfrost_diag2_end(&dev,0); assert(!reads && !logs);
    current=&a;
    for(unsigned phase=1;phase<B1S_D2_PHASES;phase++) {
        if(phase==B1S_D2_SHADER_POLLED) panfrost_diag2_polled(&dev,phase,7,3,-110);
        else panfrost_diag2_mark(&dev,phase);
    }
    assert(reads==26);
    panfrost_diag2_polled(&dev,B1S_D2_SHADER_POLLED,0,0,0); /* duplicate never rereads */
    assert(reads==26 && dev.b1s_diag2.sample[B1S_D2_SHADER_POLLED].target==7);
    assert(dev.b1s_diag2.sample[B1S_D2_SHADER_POLLED].val==3);
    assert(dev.b1s_diag2.sample[B1S_D2_SHADER_POLLED].rc==-110);
    pthread_t thread; assert(!pthread_create(&thread,NULL,record_irq,NULL));
    assert(!pthread_join(thread,NULL));
    panfrost_diag2_irq(&dev,0,0,0); /* first IRQ remains intact */
    assert(dev.b1s_diag2.irq.fs==0x008403af && !irq_logs);
    unsigned before=reads; dev.iomem=NULL; /* end remains safe without MMIO */
    panfrost_diag2_end(&dev,0);
    assert(reads==before && irq_logs==1 && logs==16);
    assert(atomic_read(&dev.b1s_diag2.state)==2 && dev.b1s_diag2.owner==NULL);
    panfrost_diag2_begin(&dev); panfrost_diag2_mark(&dev,B1S_D2_L2_BEFORE);
    panfrost_diag2_end(&dev,0); assert(reads==before && logs==16);

    reset_fixture(true); atomic_set(&dev.b1s_diag_seq,256);
    panfrost_diag2_begin(&dev); panfrost_diag2_mark(&dev,B1S_D2_L2_BEFORE);
    assert(!reads && atomic_read(&dev.b1s_diag2.state)==0);

    reset_fixture(true); panfrost_diag2_begin(&dev);
    panfrost_diag2_mark(&dev,0); panfrost_diag2_mark(&dev,B1S_D2_PHASES);
    assert(!reads);
    atomic_set(&dev.b1s_diag2.irq_state,1); /* IRQ publication still pending */
    dev.iomem=NULL; panfrost_diag2_end(&dev,-1);
    assert(!reads && !irq_logs && logs==2);

    reset_fixture(true); panfrost_diag2_begin(&dev);
    atomic_set(&dev.b1s_diag_seq,256);
    panfrost_diag2_mark(&dev,B1S_D2_L2_BEFORE); panfrost_diag2_end(&dev,0);
    assert(!reads && !logs && notices==1);
    puts("disabled/probe/foreign-owner/first-resume/poll-values/IRQ-publication/end-no-MMIO/budget/phase-bounds: PASS");
}
'''
    source = WORK / 'validation/observation-fixture.c'
    binary = WORK / 'validation/observation-fixture'
    source.write_text(program)
    subprocess.run(['gcc-12', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-pthread', str(source), '-o', str(binary)], check=True)
    result = subprocess.check_output([str(binary)], text=True)
    report = {'passed': True, 'result': result.strip(),
              'scope': 'Userspace mocks of actual helpers; not live IRQ/MMIO/GPU or hardware ordering proof'}
    (WORK / 'validation/observation-test.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
