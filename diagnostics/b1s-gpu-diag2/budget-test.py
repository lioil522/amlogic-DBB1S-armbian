#!/usr/bin/env python3
"""User-space stress test of the exact saturating helper, with atomic stubs."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

WORK = Path(os.environ['B1S_DIAG_WORK']).resolve()
header = (WORK / "source/drivers/gpu/drm/panfrost/panfrost_diag.h").read_text()
helper = re.search(r"static inline int panfrost_diag_take\(.*?\n\}", header, re.S).group(0)
limit = re.search(r"#define PANFROST_B1S_DIAG_LIMIT \d+", header).group(0)
program = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <pthread.h>
#include <assert.h>
#include <stdio.h>
typedef struct { atomic_int counter; } atomic_t;
struct panfrost_device { bool b1s_diag_enabled; atomic_t b1s_diag_seq; struct { void *dev; } base; };
static atomic_int notices, events;
static int atomic_fetch_add_unless(atomic_t *v, int a, int u) {
    int old = atomic_load(&v->counter);
    while (old != u && !atomic_compare_exchange_weak(&v->counter, &old, old + a)) {}
    return old;
}
#define dev_info(dev, ...) ((void)(dev), (void)atomic_fetch_add(&notices, 1))
'''+limit+'\n'+helper+r'''
static struct panfrost_device dev = { .b1s_diag_enabled = true };
static atomic_int seen[257];
static void *worker(void *unused) {
    (void)unused;
    for (int i=0; i<200000; i++) {
        int seq=panfrost_diag_take(&dev);
        assert(seq>=0 && seq<=256);
        if(seq) { atomic_fetch_add(&events,1); atomic_fetch_add(&seen[seq],1); }
    }
    return NULL;
}
int main(void) {
    dev.b1s_diag_enabled=false;
    assert(panfrost_diag_take(&dev)==0);
    assert(atomic_load(&dev.b1s_diag_seq.counter)==0);
    dev.b1s_diag_enabled=true;
    pthread_t threads[8];
    for(int i=0;i<8;i++) assert(pthread_create(&threads[i],NULL,worker,NULL)==0);
    for(int i=0;i<8;i++) assert(pthread_join(threads[i],NULL)==0);
    assert(atomic_load(&events)==256);
    assert(atomic_load(&notices)==1);
    assert(atomic_load(&dev.b1s_diag_seq.counter)==257);
    for(int i=1;i<=256;i++) assert(atomic_load(&seen[i])==1);
    assert(panfrost_diag_take(&dev)==0);
    puts("8 threads / 1600000 calls: 256 unique events, 1 notice, saturated at 257: PASS");
}
'''
(WORK / 'validation').mkdir(exist_ok=True)
temp_root = os.environ.get('RUNNER_TEMP') or '/tmp/opencode'
with tempfile.TemporaryDirectory(prefix="b1s-diag-budget.", dir=temp_root) as directory:
    source = Path(directory) / "test.c"
    binary = Path(directory) / "test"
    source.write_text(program)
    subprocess.run(["gcc-12", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
        "-pthread", str(source), "-o", str(binary)], check=True)
    result = subprocess.check_output([str(binary)], text=True)
    print(result, end="")
    (WORK / "validation/budget-test.json").write_text(json.dumps({"result": result.strip(),
        "scope": "userspace C11 atomic stub test of actual helper; not kernel/runtime GPU testing"}, indent=2) + "\n")
