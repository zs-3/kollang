#ifndef KOL_CONC_H
#define KOL_CONC_H

/* ============================================================================
 * Kol concurrency runtime
 *
 *   - a lazily started thread pool (one worker per CPU, KOL_THREADS to override)
 *     that grows while tasks are blocked, so a blocked task can never starve
 *     the tasks that would unblock it
 *   - futures:   spawn f(x) / await fut        (await helps: it runs a task that
 *                                               has not started yet on the
 *                                               calling thread)
 *   - channels:  bounded FIFO queues, close(), blocking send/receive
 *   - select:    wait on several channel operations / a timeout
 *   - mutex and atomic integers
 *   - deadlock detection: "all tasks are asleep" is reported instead of hanging
 *
 * Included by kol_runtime.h when the program defines KOL_USE_CONCURRENCY.
 * ============================================================================ */

#include <pthread.h>
#include <time.h>
#include <errno.h>
#include <unistd.h>
#include <sched.h>

#define KOL_FN static __attribute__((unused))

#define KOL_MAX_WORKERS     1024
#define KOL_WAIT_SLICE_MS   250     /* blocked threads re-check for deadlock this often */
#define KOL_DEADLOCK_MS     800     /* a stuck state must persist this long to be a deadlock */
#define KOL_IDLE_RETIRE_MS  1000    /* surplus idle workers exit after this long */

/* ---------------------------------------------------------------------------
 * Time helpers
 * ------------------------------------------------------------------------- */

KOL_FN int64_t kol_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Absolute CLOCK_REALTIME deadline `ms` from now (for pthread_cond_timedwait). */
KOL_FN void kol_deadline_after(struct timespec* ts, int64_t ms) {
    clock_gettime(CLOCK_REALTIME, ts);
    ts->tv_sec += ms / 1000;
    ts->tv_nsec += (ms % 1000) * 1000000L;
    if (ts->tv_nsec >= 1000000000L) { ts->tv_sec++; ts->tv_nsec -= 1000000000L; }
}

KOL_FN int64_t kol_cpu_count(void) {
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n < 1 ? 1 : (int64_t)n;
}

/* ---------------------------------------------------------------------------
 * Scheduler state
 * ------------------------------------------------------------------------- */

typedef struct KolFuture KolFuture;
typedef void (*KolRunFn)(void* args, void* result);
typedef void (*KolDtorFn)(void* result);

struct KolFuture {
    KolObj obj;                 /* must be first */
    int state;                  /* 0 pending, 1 running, 2 done (atomic) */
    KolRunFn run;
    KolDtorFn res_dtor;         /* releases the result value (strings, lists...) */
    void* args;
    void* result;
    KolFuture* next;            /* run-queue link */
};

typedef struct {
    pthread_mutex_t mu;
    pthread_cond_t  work_cv;
    KolFuture* head;
    KolFuture* tail;
    int workers;                /* live worker threads */
    int idle;                   /* workers waiting for work */
    int blocked_workers;        /* workers currently blocked/sleeping inside a task */
    int waiting;                /* threads (incl. main) blocked with no timeout */
    int target;                 /* desired number of runnable workers */
    uint64_t epoch;             /* bumped on every scheduling event (progress) */
    uint64_t dl_epoch;          /* epoch at which the stuck state was first seen */
    int64_t  dl_since;          /* when it was first seen (0 = not stuck) */
    int initialised;
    int detect_deadlock;
} KolSched;

static KolSched kol_sched = { .mu = PTHREAD_MUTEX_INITIALIZER, .work_cv = PTHREAD_COND_INITIALIZER };
static _Thread_local int kol_tl_worker = 0;

#define KOL_FSTRIPES 64
static pthread_mutex_t kol_fmu[KOL_FSTRIPES];
static pthread_cond_t  kol_fcv[KOL_FSTRIPES];

static inline unsigned kol_fstripe(const void* p) { return (unsigned)(((uintptr_t)p >> 6) % KOL_FSTRIPES); }

KOL_FN void kol_sched_init_locked(void) {
    KolSched* s = &kol_sched;
    if (s->initialised) return;
    s->initialised = 1;
    for (int i = 0; i < KOL_FSTRIPES; i++) {
        pthread_mutex_init(&kol_fmu[i], NULL);
        pthread_cond_init(&kol_fcv[i], NULL);
    }
    int64_t n = kol_cpu_count();
    const char* env = getenv("KOL_THREADS");
    if (env && atoi(env) > 0) n = atoi(env);
    s->target = (int)n;
    const char* nd = getenv("KOL_NO_DEADLOCK_DETECT");
    s->detect_deadlock = !(nd && nd[0] && nd[0] != '0');
}

KOL_FN void kol_deadlock_panic(void) {
    fflush(stdout);
    fprintf(stderr, "Kol Panic: all tasks are asleep - deadlock!\n");
    exit(2);
}

/* Called with kol_sched.mu held by a thread that is *counted as waiting*. */
KOL_FN void kol_deadlock_check_locked(void) {
    KolSched* s = &kol_sched;
    if (!s->detect_deadlock) return;
    int total = 1 + s->workers;                  /* main + workers */
    int runnable = total - s->idle - s->waiting; /* threads that can make progress */
    if (runnable <= 0 && s->head == NULL) {
        int64_t now = kol_now_ms();
        if (s->dl_since == 0 || s->dl_epoch != s->epoch) {
            s->dl_epoch = s->epoch;
            s->dl_since = now;
        } else if (now - s->dl_since >= KOL_DEADLOCK_MS) {
            kol_deadlock_panic();
        }
    } else {
        s->dl_since = 0;
    }
}

KOL_FN void* kol_worker_main(void* arg);

KOL_FN void kol_start_worker_locked(void) {
    KolSched* s = &kol_sched;
    if (s->workers >= KOL_MAX_WORKERS) return;
    pthread_t t;
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
    if (!kol_mt) kol_mt = 1;   /* from here on, reference counts are atomic (written only before the first thread exists) */
    if (pthread_create(&t, &at, kol_worker_main, NULL) == 0) s->workers++;
    pthread_attr_destroy(&at);
}

/* A thread is about to wait for something. `timed` waits (sleep, timeouts) will
 * end by themselves; untimed waits are deadlock candidates. A blocked worker
 * hands its CPU slot to a replacement worker if tasks are queued. */
KOL_FN void kol_block_begin(int timed) {
    KolSched* s = &kol_sched;
    pthread_mutex_lock(&s->mu);
    kol_sched_init_locked();
    if (kol_tl_worker) {
        s->blocked_workers++;
        if (s->head && s->idle == 0 && s->workers - s->blocked_workers < s->target)
            kol_start_worker_locked();
    }
    if (!timed) s->waiting++;
    pthread_mutex_unlock(&s->mu);
}

/* `slice_expired`: the wait ended because the re-check slice elapsed, not because
 * of a wake-up. That is when we look for a deadlock. */
KOL_FN void kol_block_end(int timed, int slice_expired) {
    KolSched* s = &kol_sched;
    pthread_mutex_lock(&s->mu);
    if (!timed && slice_expired) kol_deadlock_check_locked();
    if (!slice_expired) s->epoch++;
    if (!timed) s->waiting--;
    if (kol_tl_worker) s->blocked_workers--;
    pthread_mutex_unlock(&s->mu);
}

/* Wait on `cv` (caller holds `mu`). Returns 1 if the re-check slice expired
 * (callers simply loop and re-test their condition). */
KOL_FN int kol_cond_wait(pthread_cond_t* cv, pthread_mutex_t* mu) {
    struct timespec ts;
    kol_deadline_after(&ts, KOL_WAIT_SLICE_MS);
    kol_block_begin(0);
    int rc = pthread_cond_timedwait(cv, mu, &ts);
    kol_block_end(0, rc == ETIMEDOUT);
    return rc == ETIMEDOUT;
}

/* Wait on `cv` until `deadline_ms` (kol_now_ms() scale). Returns 0 once the
 * deadline has passed. Caller holds `mu`. */
KOL_FN int kol_cond_wait_until(pthread_cond_t* cv, pthread_mutex_t* mu, int64_t deadline_ms) {
    int64_t remaining = deadline_ms - kol_now_ms();
    if (remaining <= 0) return 0;
    struct timespec ts;
    kol_deadline_after(&ts, remaining);
    kol_block_begin(1);
    pthread_cond_timedwait(cv, mu, &ts);
    kol_block_end(1, 0);
    return 1;
}

KOL_FN void kol_sleep_ms(int64_t ms) {
    if (ms <= 0) { sched_yield(); return; }
    kol_block_begin(1);
    struct timespec req = { (time_t)(ms / 1000), (long)((ms % 1000) * 1000000L) }, rem;
    while (nanosleep(&req, &rem) == -1 && errno == EINTR) req = rem;
    kol_block_end(1, 0);
}

KOL_FN void kol_yield(void) { sched_yield(); }

/* ---------------------------------------------------------------------------
 * Futures and the worker pool
 * ------------------------------------------------------------------------- */

KOL_FN void kol_future_destroy(KolObj* o) {
    KolFuture* f = (KolFuture*)o;
    if (f->res_dtor && __atomic_load_n(&f->state, __ATOMIC_ACQUIRE) == 2) f->res_dtor(f->result);
    free(f);
}

/* Allocates a future with `args_size` bytes of argument storage and `res_size`
 * bytes of result storage in one block. Initial refs: 1 for the handle, 1 for
 * the run queue (consumed by kol_future_submit). */
KOL_FN KolFuture* kol_future_new(KolRunFn run, size_t args_size, size_t res_size, KolDtorFn dtor) {
    size_t hdr = (sizeof(KolFuture) + 15) & ~(size_t)15;
    size_t a = (args_size + 15) & ~(size_t)15;
    size_t r = (res_size + 15) & ~(size_t)15;
    KolFuture* f = (KolFuture*)calloc(1, hdr + a + r + 16);
    if (!f) kol_rt_panic("out of memory creating a task");
    f->obj.refs = 2;
    f->obj.destroy = kol_future_destroy;
    f->run = run;
    f->res_dtor = dtor;
    f->args = (char*)f + hdr;
    f->result = (char*)f + hdr + a;
    return f;
}

KOL_FN int kol_future_try_claim(KolFuture* f) {
    int expected = 0;
    return __atomic_compare_exchange_n(&f->state, &expected, 1, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}

KOL_FN void kol_future_execute(KolFuture* f) {   /* caller has claimed f */
    f->run(f->args, f->result);
    unsigned i = kol_fstripe(f);
    pthread_mutex_lock(&kol_fmu[i]);
    __atomic_store_n(&f->state, 2, __ATOMIC_SEQ_CST);
    pthread_cond_broadcast(&kol_fcv[i]);
    pthread_mutex_unlock(&kol_fmu[i]);
    pthread_mutex_lock(&kol_sched.mu);
    kol_sched.epoch++;
    pthread_mutex_unlock(&kol_sched.mu);
}

KOL_FN void kol_future_submit(KolFuture* f) {
    KolSched* s = &kol_sched;
    pthread_mutex_lock(&s->mu);
    kol_sched_init_locked();
    f->next = NULL;
    if (s->tail) s->tail->next = f; else s->head = f;
    s->tail = f;
    s->epoch++;
    if (s->idle > 0) pthread_cond_signal(&s->work_cv);
    else if (s->workers - s->blocked_workers < s->target) kol_start_worker_locked();
    pthread_mutex_unlock(&s->mu);
}

KOL_FN void* kol_worker_main(void* arg) {
    (void)arg;
    KolSched* s = &kol_sched;
    kol_tl_worker = 1;
    pthread_mutex_lock(&s->mu);
    for (;;) {
        while (s->head == NULL) {
            if (s->workers - s->blocked_workers > s->target) {   /* surplus worker retires */
                s->workers--;
                pthread_mutex_unlock(&s->mu);
                return NULL;
            }
            struct timespec ts;
            kol_deadline_after(&ts, KOL_IDLE_RETIRE_MS);
            s->idle++;
            pthread_cond_timedwait(&s->work_cv, &s->mu, &ts);
            s->idle--;
        }
        KolFuture* f = s->head;
        s->head = f->next;
        if (!s->head) s->tail = NULL;
        s->epoch++;
        pthread_mutex_unlock(&s->mu);

        if (kol_future_try_claim(f)) kol_future_execute(f);   /* else an awaiter already ran it */
        kol_obj_release(f);                                  /* the run queue's reference */

        pthread_mutex_lock(&s->mu);
    }
    return NULL;
}

/* Blocks until the future is done and returns a pointer to its result. A task
 * that has not started yet is run right here instead of waiting for a worker. */
KOL_FN void* kol_future_wait(KolFuture* f) {
    if (__atomic_load_n(&f->state, __ATOMIC_ACQUIRE) == 2) return f->result;
    if (kol_future_try_claim(f)) {
        kol_future_execute(f);
        return f->result;
    }
    unsigned i = kol_fstripe(f);
    pthread_mutex_lock(&kol_fmu[i]);
    while (__atomic_load_n(&f->state, __ATOMIC_SEQ_CST) != 2)
        kol_cond_wait(&kol_fcv[i], &kol_fmu[i]);
    pthread_mutex_unlock(&kol_fmu[i]);
    return f->result;
}

KOL_FN bool kol_future_done(KolFuture* f) {
    return __atomic_load_n(&f->state, __ATOMIC_ACQUIRE) == 2;
}

/* ---------------------------------------------------------------------------
 * select support: one global "something happened on some channel" epoch.
 * Channel operations only touch it while a select is actually waiting.
 * ------------------------------------------------------------------------- */

static pthread_mutex_t kol_sel_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  kol_sel_cv = PTHREAD_COND_INITIALIZER;
static uint64_t        kol_sel_epoch = 0;
static int             kol_sel_waiters = 0;

KOL_FN void kol_select_notify(void) {
    if (__atomic_load_n(&kol_sel_waiters, __ATOMIC_SEQ_CST) == 0) return;
    pthread_mutex_lock(&kol_sel_mu);
    kol_sel_epoch++;
    pthread_cond_broadcast(&kol_sel_cv);
    pthread_mutex_unlock(&kol_sel_mu);
}

KOL_FN void kol_select_enter(void) { __atomic_add_fetch(&kol_sel_waiters, 1, __ATOMIC_SEQ_CST); }
KOL_FN void kol_select_leave(void) { __atomic_sub_fetch(&kol_sel_waiters, 1, __ATOMIC_SEQ_CST); }

KOL_FN uint64_t kol_select_epoch(void) {
    pthread_mutex_lock(&kol_sel_mu);
    uint64_t e = kol_sel_epoch;
    pthread_mutex_unlock(&kol_sel_mu);
    return e;
}

/* Waits until the epoch moves past `seen`. deadline_ms < 0 means no timeout.
 * Returns 0 if the deadline passed first. */
KOL_FN int kol_select_wait(uint64_t seen, int64_t deadline_ms) {
    int woken = 1;
    pthread_mutex_lock(&kol_sel_mu);
    while (kol_sel_epoch == seen) {
        if (deadline_ms >= 0) {
            if (!kol_cond_wait_until(&kol_sel_cv, &kol_sel_mu, deadline_ms)) { woken = 0; break; }
        } else {
            kol_cond_wait(&kol_sel_cv, &kol_sel_mu);
        }
    }
    pthread_mutex_unlock(&kol_sel_mu);
    return woken;
}

/* Cheap per-thread xorshift so select does not always prefer its first case. */
KOL_FN uint32_t kol_select_rand(uint32_t n) {
    static _Thread_local uint32_t x = 2463534242u;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return n ? x % n : 0;
}

/* ---------------------------------------------------------------------------
 * Channels: bounded FIFO ring buffer
 * ------------------------------------------------------------------------- */

#if defined(__x86_64__) || defined(__i386__)
#define KOL_CPU_RELAX() __builtin_ia32_pause()
#elif defined(__aarch64__)
#define KOL_CPU_RELAX() __asm__ __volatile__("yield")
#else
#define KOL_CPU_RELAX() ((void)0)
#endif

#define KOL_SPIN_ITERS 2000

static int kol_spin_ok(void) {
    static int cached = -1;   /* idempotent lazy init; accessed atomically */
    int c = __atomic_load_n(&cached, __ATOMIC_RELAXED);
    if (c < 0) {
        c = kol_cpu_count() > 1;
        __atomic_store_n(&cached, c, __ATOMIC_RELAXED);
    }
    return c;
}

#define KOL_ELEM_PLAIN 0
#define KOL_ELEM_STR   1   /* elements are KolStr: release leftovers on destroy */

typedef struct KolChannel {
    KolObj obj;                 /* must be first */
    pthread_mutex_t mu;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
    char* buf;
    size_t elem_size;
    size_t cap;
    size_t len;
    size_t head;
    int closed;
    int elem_kind;
} KolChannel;

KOL_FN void kol_channel_destroy(KolObj* o) {
    KolChannel* ch = (KolChannel*)o;
    if (ch->elem_kind == KOL_ELEM_STR) {
        for (size_t i = 0; i < ch->len; i++)
            kol_arc_release_str(*(KolStr*)(ch->buf + ((ch->head + i) % ch->cap) * ch->elem_size));
    }
    pthread_mutex_destroy(&ch->mu);
    pthread_cond_destroy(&ch->not_empty);
    pthread_cond_destroy(&ch->not_full);
    free(ch->buf);
    free(ch);
}

KOL_FN KolChannel* kol_channel_create(size_t elem_size, int64_t cap, int elem_kind) {
    if (cap < 1) kol_rt_panic("channel capacity must be at least 1");
    KolChannel* ch = (KolChannel*)calloc(1, sizeof(KolChannel));
    if (!ch) kol_rt_panic("out of memory creating a channel");
    ch->buf = (char*)malloc(elem_size * (size_t)cap);
    if (!ch->buf) kol_rt_panic("out of memory creating a channel");
    ch->obj.refs = 1;
    ch->obj.destroy = kol_channel_destroy;
    ch->elem_size = elem_size;
    ch->cap = (size_t)cap;
    ch->elem_kind = elem_kind;
    pthread_mutex_init(&ch->mu, NULL);
    pthread_cond_init(&ch->not_empty, NULL);
    pthread_cond_init(&ch->not_full, NULL);
    return ch;
}

static inline void kol_channel_push_locked(KolChannel* ch, const void* value) {
    memcpy(ch->buf + ((ch->head + ch->len) % ch->cap) * ch->elem_size, value, ch->elem_size);
    __atomic_store_n(&ch->len, ch->len + 1, __ATOMIC_RELEASE);
    pthread_cond_signal(&ch->not_empty);
}

static inline void kol_channel_pop_locked(KolChannel* ch, void* out) {
    memcpy(out, ch->buf + ch->head * ch->elem_size, ch->elem_size);
    ch->head = (ch->head + 1) % ch->cap;
    __atomic_store_n(&ch->len, ch->len - 1, __ATOMIC_RELEASE);
    pthread_cond_signal(&ch->not_full);
}

/* Blocks while the channel is full. Sending on a closed channel is a panic. */
KOL_FN void kol_channel_send(KolChannel* ch, const void* value) {
    pthread_mutex_lock(&ch->mu);
    if (ch->len == ch->cap && !ch->closed && kol_spin_ok()) {
        /* Briefly spin before sleeping: the receiver is usually about to make room. */
        pthread_mutex_unlock(&ch->mu);
        for (int i = 0; i < KOL_SPIN_ITERS; i++) {
            if (__atomic_load_n(&ch->len, __ATOMIC_ACQUIRE) < ch->cap || __atomic_load_n(&ch->closed, __ATOMIC_ACQUIRE)) break;
            KOL_CPU_RELAX();
        }
        pthread_mutex_lock(&ch->mu);
    }
    while (ch->len == ch->cap && !ch->closed) kol_cond_wait(&ch->not_full, &ch->mu);
    if (ch->closed) {
        pthread_mutex_unlock(&ch->mu);
        kol_rt_panic("send on closed channel");
    }
    kol_channel_push_locked(ch, value);
    pthread_mutex_unlock(&ch->mu);
    kol_select_notify();
}

/* Blocks while the channel is empty. Returns 1 and fills `out`, or returns 0
 * (with `out` zeroed) once the channel is closed and drained. */
KOL_FN int kol_channel_recv(KolChannel* ch, void* out) {
    pthread_mutex_lock(&ch->mu);
    if (ch->len == 0 && !ch->closed && kol_spin_ok()) {
        /* Briefly spin before sleeping: the sender is usually about to deliver. */
        pthread_mutex_unlock(&ch->mu);
        for (int i = 0; i < KOL_SPIN_ITERS; i++) {
            if (__atomic_load_n(&ch->len, __ATOMIC_ACQUIRE) != 0 || __atomic_load_n(&ch->closed, __ATOMIC_ACQUIRE)) break;
            KOL_CPU_RELAX();
        }
        pthread_mutex_lock(&ch->mu);
    }
    while (ch->len == 0 && !ch->closed) kol_cond_wait(&ch->not_empty, &ch->mu);
    if (ch->len == 0) {
        pthread_mutex_unlock(&ch->mu);
        memset(out, 0, ch->elem_size);
        return 0;
    }
    kol_channel_pop_locked(ch, out);
    pthread_mutex_unlock(&ch->mu);
    kol_select_notify();
    return 1;
}

/* Non-blocking send: 1 = sent, 0 = full. Panics if closed. */
KOL_FN int kol_channel_try_send(KolChannel* ch, const void* value) {
    pthread_mutex_lock(&ch->mu);
    if (ch->closed) {
        pthread_mutex_unlock(&ch->mu);
        kol_rt_panic("send on closed channel");
    }
    if (ch->len == ch->cap) { pthread_mutex_unlock(&ch->mu); return 0; }
    kol_channel_push_locked(ch, value);
    pthread_mutex_unlock(&ch->mu);
    kol_select_notify();
    return 1;
}

/* Non-blocking receive: 1 = got a value, 0 = nothing yet, -1 = closed and drained. */
KOL_FN int kol_channel_try_recv(KolChannel* ch, void* out) {
    pthread_mutex_lock(&ch->mu);
    if (ch->len == 0) {
        int closed = ch->closed;
        pthread_mutex_unlock(&ch->mu);
        if (closed) { memset(out, 0, ch->elem_size); return -1; }
        return 0;
    }
    kol_channel_pop_locked(ch, out);
    pthread_mutex_unlock(&ch->mu);
    kol_select_notify();
    return 1;
}

/* Closing is idempotent. Receivers drain what is buffered, then see "closed". */
KOL_FN void kol_channel_close(KolChannel* ch) {
    pthread_mutex_lock(&ch->mu);
    __atomic_store_n(&ch->closed, 1, __ATOMIC_RELEASE);
    pthread_cond_broadcast(&ch->not_empty);
    pthread_cond_broadcast(&ch->not_full);
    pthread_mutex_unlock(&ch->mu);
    kol_select_notify();
}

KOL_FN int64_t kol_channel_len(KolChannel* ch) {
    pthread_mutex_lock(&ch->mu);
    int64_t n = (int64_t)ch->len;
    pthread_mutex_unlock(&ch->mu);
    return n;
}
KOL_FN int64_t kol_channel_cap(KolChannel* ch) { return (int64_t)ch->cap; }
KOL_FN bool kol_channel_is_closed(KolChannel* ch) {
    pthread_mutex_lock(&ch->mu);
    bool c = ch->closed != 0;
    pthread_mutex_unlock(&ch->mu);
    return c;
}

/* ---------------------------------------------------------------------------
 * Mutex (blocking participates in deadlock detection) and atomic integers
 * ------------------------------------------------------------------------- */

typedef struct KolMutex {
    KolObj obj;                 /* must be first */
    pthread_mutex_t mu;
    pthread_cond_t cv;
    int locked;
} KolMutex;

KOL_FN void kol_mutex_destroy(KolObj* o) {
    KolMutex* m = (KolMutex*)o;
    pthread_mutex_destroy(&m->mu);
    pthread_cond_destroy(&m->cv);
    free(m);
}

KOL_FN KolMutex* kol_mutex_create(void) {
    KolMutex* m = (KolMutex*)calloc(1, sizeof(KolMutex));
    if (!m) kol_rt_panic("out of memory creating a mutex");
    m->obj.refs = 1;
    m->obj.destroy = kol_mutex_destroy;
    pthread_mutex_init(&m->mu, NULL);
    pthread_cond_init(&m->cv, NULL);
    return m;
}

KOL_FN void kol_mutex_lock(KolMutex* m) {
    pthread_mutex_lock(&m->mu);
    while (m->locked) kol_cond_wait(&m->cv, &m->mu);
    m->locked = 1;
    pthread_mutex_unlock(&m->mu);
}

KOL_FN bool kol_mutex_try_lock(KolMutex* m) {
    bool got = false;
    pthread_mutex_lock(&m->mu);
    if (!m->locked) { m->locked = 1; got = true; }
    pthread_mutex_unlock(&m->mu);
    return got;
}

KOL_FN void kol_mutex_unlock(KolMutex* m) {
    pthread_mutex_lock(&m->mu);
    if (!m->locked) {
        pthread_mutex_unlock(&m->mu);
        kol_rt_panic("unlock of an unlocked mutex");
    }
    m->locked = 0;
    pthread_cond_signal(&m->cv);
    pthread_mutex_unlock(&m->mu);
}

typedef struct KolAtomic {
    KolObj obj;                 /* must be first */
    int64_t value;
} KolAtomic;

KOL_FN void kol_atomic_destroy(KolObj* o) { free(o); }

KOL_FN KolAtomic* kol_atomic_create(int64_t initial) {
    KolAtomic* a = (KolAtomic*)calloc(1, sizeof(KolAtomic));
    if (!a) kol_rt_panic("out of memory creating an atomic");
    a->obj.refs = 1;
    a->obj.destroy = kol_atomic_destroy;
    a->value = initial;
    return a;
}

KOL_FN int64_t kol_atomic_load(KolAtomic* a)              { return __atomic_load_n(&a->value, __ATOMIC_SEQ_CST); }
KOL_FN void    kol_atomic_store(KolAtomic* a, int64_t v)  { __atomic_store_n(&a->value, v, __ATOMIC_SEQ_CST); }
KOL_FN int64_t kol_atomic_add(KolAtomic* a, int64_t v)    { return __atomic_add_fetch(&a->value, v, __ATOMIC_SEQ_CST); }
KOL_FN int64_t kol_atomic_sub(KolAtomic* a, int64_t v)    { return __atomic_sub_fetch(&a->value, v, __ATOMIC_SEQ_CST); }
KOL_FN int64_t kol_atomic_swap(KolAtomic* a, int64_t v)   { return __atomic_exchange_n(&a->value, v, __ATOMIC_SEQ_CST); }
KOL_FN bool    kol_atomic_cas(KolAtomic* a, int64_t expected, int64_t desired) {
    return __atomic_compare_exchange_n(&a->value, &expected, desired, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}

#endif /* KOL_CONC_H */
