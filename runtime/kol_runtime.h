#ifndef KOL_RUNTIME_H
#define KOL_RUNTIME_H

#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <ctype.h>
#include <math.h>
#include <pthread.h>
#include <sys/stat.h>
#ifndef _WIN32
#include <dirent.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netdb.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/time.h>
#include <sys/wait.h>
#else
#include <direct.h>
#include <io.h>
#endif
#ifndef _WIN32
#include <regex.h>
#endif

/* ============================================================================
 * PANIC AND ERRORS
 * ============================================================================ */

static inline void kol_panic(const char* file, int line, const char* message) {
    fflush(stdout);
    fprintf(stderr, "Kol Panic at %s:%d: %s\n", file, line, message);
    exit(1);
}

/* Panic without a source location (used by the runtime itself). */
static inline void kol_rt_panic(const char* message) {
    fflush(stdout);
    fprintf(stderr, "Kol Panic: %s\n", message);
    exit(1);
}

/* ============================================================================
 * THREAD MODE + REFCOUNTED HANDLE OBJECTS
 *
 * kol_mt is set to 1 *before* the first worker thread is created. Until then the
 * program is single-threaded and reference counts use plain (fast) operations;
 * afterwards every refcount operation is atomic, so strings and handles can be
 * shared safely between tasks.
 * ============================================================================ */

static int kol_mt = 0;

static inline uint32_t kol_rc_inc(uint32_t* rc) {
    if (kol_mt) return __atomic_add_fetch(rc, 1, __ATOMIC_RELAXED);
    return ++(*rc);
}
static inline uint32_t kol_rc_dec(uint32_t* rc) {
    if (kol_mt) return __atomic_sub_fetch(rc, 1, __ATOMIC_ACQ_REL);
    return --(*rc);
}
static inline uint32_t kol_rc_get(uint32_t* rc) {
    if (kol_mt) return __atomic_load_n(rc, __ATOMIC_ACQUIRE);
    return *rc;
}

/* Every shared runtime object (channel, future, mutex, atomic) starts with this. */
typedef struct KolObj {
    uint32_t refs;
    void (*destroy)(struct KolObj*);
} KolObj;

static inline void kol_obj_retain(void* p) {
    if (p) kol_rc_inc(&((KolObj*)p)->refs);
}
static inline void kol_obj_release(void* p) {
    if (!p) return;
    KolObj* o = (KolObj*)p;
    if (kol_rc_dec(&o->refs) == 0) o->destroy(o);
}

/* ============================================================================
 * STRINGS AND SSO (Small String Optimization)
 * ============================================================================ */

#define KOL_SSO_MAX 14

typedef struct {
    uint32_t ref_count;
    size_t len;
    size_t capacity;
    char* data;
} KolStrHeap;

typedef struct {
    bool is_heap;
    union {
        struct {
            uint8_t len;
            char data[KOL_SSO_MAX + 1];
        } sso;
        struct {
            KolStrHeap* ptr;
        } heap;
    };
} KolStr;

static inline KolStr kol_str_create(const char* cstr) {
    KolStr s;
    if (!cstr) cstr = "";
    size_t len = strlen(cstr);
    if (len <= KOL_SSO_MAX) {
        s.is_heap = false;
        s.sso.len = (uint8_t)len;
        memcpy(s.sso.data, cstr, len);
        s.sso.data[len] = '\0';
    } else {
        s.is_heap = true;
        size_t cap = len * 2 + 16;
        KolStrHeap* h = (KolStrHeap*)malloc(sizeof(KolStrHeap));
        char* buf = (char*)malloc(cap + 1);
        if (!h || !buf) {
            fprintf(stderr, "Out of memory in kol_str_create\n");
            exit(1);
        }
        h->ref_count = 1;
        h->len = len;
        h->capacity = cap;
        memcpy(buf, cstr, len);
        buf[len] = '\0';
        h->data = buf;
        s.heap.ptr = h;
    }
    return s;
}

static inline const char* kol_str_cstr(const KolStr* s) {
    if (!s->is_heap) {
        return s->sso.data;
    }
    return s->heap.ptr->data;
}

static inline size_t kol_str_len(KolStr s) {
    if (!s.is_heap) return s.sso.len;
    return s.heap.ptr ? s.heap.ptr->len : 0;
}

static inline const char* kol_str_cstr_tmp(KolStr s) {
    static _Thread_local char bufs[4][256];
    static _Thread_local int buf_idx = 0;
    buf_idx = (buf_idx + 1) % 4;
    const char* cs = kol_str_cstr(&s);
    snprintf(bufs[buf_idx], sizeof(bufs[buf_idx]), "%s", cs);
    return bufs[buf_idx];
}

static inline bool kol_str_contains(KolStr s, KolStr sub) {
    const char* cs = kol_str_cstr(&s);
    const char* csub = kol_str_cstr(&sub);
    return strstr(cs, csub) != NULL;
}

static inline bool kol_str_starts_with(KolStr s, KolStr prefix) {
    const char* cs = kol_str_cstr(&s);
    const char* cp = kol_str_cstr(&prefix);
    size_t lp = kol_str_len(prefix);
    size_t ls = kol_str_len(s);
    if (lp > ls) return false;
    return strncmp(cs, cp, lp) == 0;
}

static inline bool kol_str_ends_with(KolStr s, KolStr suffix) {
    const char* cs = kol_str_cstr(&s);
    const char* csuf = kol_str_cstr(&suffix);
    size_t lsuf = kol_str_len(suffix);
    size_t ls = kol_str_len(s);
    if (lsuf > ls) return false;
    return strcmp(cs + (ls - lsuf), csuf) == 0;
}

static inline KolStr kol_str_replace(KolStr s, KolStr old_s, KolStr new_s) {
    const char* cs = kol_str_cstr(&s);
    const char* cold = kol_str_cstr(&old_s);
    const char* cnew = kol_str_cstr(&new_s);
    size_t len_old = kol_str_len(old_s);
    size_t len_new = kol_str_len(new_s);
    if (len_old == 0) return kol_str_create(cs);

    int count = 0;
    const char* tmp = cs;
    while ((tmp = strstr(tmp, cold))) {
        count++;
        tmp += len_old;
    }

    size_t new_len = strlen(cs) + count * (len_new - len_old);
    char* buf = (char*)malloc(new_len + 1);
    char* p = buf;
    while (*cs) {
        if (strstr(cs, cold) == cs) {
            strcpy(p, cnew);
            p += len_new;
            cs += len_old;
        } else {
            *p++ = *cs++;
        }
    }
    *p = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline KolStr kol_str_repeat(KolStr s, int64_t n) {
    if (n <= 0) return kol_str_create("");
    const char* cs = kol_str_cstr(&s);
    size_t len = kol_str_len(s);
    size_t total = len * n;
    char* buf = (char*)malloc(total + 1);
    for (int64_t i = 0; i < n; i++) {
        memcpy(buf + (i * len), cs, len);
    }
    buf[total] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline KolStr kol_str_slice(KolStr s, int64_t from, int64_t to) {
    const char* cs = kol_str_cstr(&s);
    size_t len = kol_str_len(s);
    if (from < 0) from = 0;
    if ((size_t)from >= len || to <= from) return kol_str_create("");
    if ((size_t)to > len) to = (int64_t)len;
    size_t new_len = to - from;
    char* buf = (char*)malloc(new_len + 1);
    memcpy(buf, cs + from, new_len);
    buf[new_len] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline void kol_arc_retain_str(KolStr s) {
    if (s.is_heap && s.heap.ptr) {
        kol_rc_inc(&s.heap.ptr->ref_count);
    }
}

static inline void kol_arc_release_str(KolStr s) {
    if (s.is_heap && s.heap.ptr) {
        if (kol_rc_dec(&s.heap.ptr->ref_count) == 0) {
            if (s.heap.ptr->data) free(s.heap.ptr->data);
            free(s.heap.ptr);
        }
    }
}

static inline bool kol_str_eq(KolStr a, KolStr b) {
    size_t la = kol_str_len(a);
    size_t lb = kol_str_len(b);
    if (la != lb) return false;
    return memcmp(kol_str_cstr(&a), kol_str_cstr(&b), la) == 0;
}

/* lexicographic byte comparison: <0, 0, >0 */
static inline int kol_str_cmp(KolStr a, KolStr b) {
    size_t la = kol_str_len(a);
    size_t lb = kol_str_len(b);
    size_t n = la < lb ? la : lb;
    int c = n ? memcmp(kol_str_cstr(&a), kol_str_cstr(&b), n) : 0;
    if (c != 0) return c;
    return (la > lb) - (la < lb);
}

static inline bool kol_str_neq(KolStr a, KolStr b) {
    return !kol_str_eq(a, b);
}

static inline KolStr kol_str_concat(KolStr a, KolStr b) {
    size_t la = kol_str_len(a);
    size_t lb = kol_str_len(b);
    size_t total = la + lb;

    if (a.is_heap && a.heap.ptr && kol_rc_get(&a.heap.ptr->ref_count) == 1 && total <= a.heap.ptr->capacity) {
        memcpy(a.heap.ptr->data + la, kol_str_cstr(&b), lb);
        a.heap.ptr->data[total] = '\0';
        a.heap.ptr->len = total;
        return a;
    }

    size_t new_cap = total * 2 + 16;
    char* buf = (char*)malloc(new_cap + 1);
    memcpy(buf, kol_str_cstr(&a), la);
    memcpy(buf + la, kol_str_cstr(&b), lb);
    buf[total] = '\0';

    KolStr result;
    result.is_heap = true;
    result.heap.ptr = (KolStrHeap*)malloc(sizeof(KolStrHeap));
    result.heap.ptr->data = buf;
    result.heap.ptr->len = total;
    result.heap.ptr->capacity = new_cap;
    result.heap.ptr->ref_count = 1;
    return result;
}

static inline void kol_str_append(KolStr* s, KolStr other) {
    /* Copy-on-write: never mutate a heap buffer that another variable or task shares. */
    if (s->is_heap && s->heap.ptr && kol_rc_get(&s->heap.ptr->ref_count) > 1) {
        KolStr own = kol_str_create(kol_str_cstr(s));
        kol_arc_release_str(*s);
        *s = own;
    }
    size_t la = kol_str_len(*s);
    size_t lb = kol_str_len(other);
    size_t total = la + lb;

    if (!s->is_heap) {
        if (total <= KOL_SSO_MAX) {
            memcpy(s->sso.data + la, kol_str_cstr(&other), lb);
            s->sso.data[total] = '\0';
            s->sso.len = (uint8_t)total;
            return;
        }
        size_t cap = total * 2 + 16;
        char* buf = (char*)malloc(cap + 1);
        memcpy(buf, s->sso.data, la);
        memcpy(buf + la, kol_str_cstr(&other), lb);
        buf[total] = '\0';
        s->is_heap = true;
        s->heap.ptr = (KolStrHeap*)malloc(sizeof(KolStrHeap));
        s->heap.ptr->data = buf;
        s->heap.ptr->len = total;
        s->heap.ptr->capacity = cap;
        s->heap.ptr->ref_count = 1;
        return;
    }

    if (total > s->heap.ptr->capacity) {
        size_t new_cap = total * 2 + 16;
        s->heap.ptr->data = (char*)realloc(s->heap.ptr->data, new_cap + 1);
        s->heap.ptr->capacity = new_cap;
    }
    memcpy(s->heap.ptr->data + la, kol_str_cstr(&other), lb);
    s->heap.ptr->data[total] = '\0';
    s->heap.ptr->len = total;
}

static inline KolStr kol_str_upper(KolStr s) {
    const char* cs = kol_str_cstr(&s);
    size_t len = kol_str_len(s);
    char* buf = (char*)malloc(len + 1);
    for (size_t i = 0; i < len; i++) {
        buf[i] = (char)toupper((unsigned char)cs[i]);
    }
    buf[len] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline KolStr kol_str_lower(KolStr s) {
    const char* cs = kol_str_cstr(&s);
    size_t len = kol_str_len(s);
    char* buf = (char*)malloc(len + 1);
    for (size_t i = 0; i < len; i++) {
        buf[i] = (char)tolower((unsigned char)cs[i]);
    }
    buf[len] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline KolStr kol_str_trim(KolStr s) {
    const char* cs = kol_str_cstr(&s);
    size_t len = kol_str_len(s);
    size_t start = 0;
    while (start < len && isspace((unsigned char)cs[start])) start++;
    size_t end = len;
    while (end > start && isspace((unsigned char)cs[end - 1])) end--;
    size_t new_len = end - start;
    char* buf = (char*)malloc(new_len + 1);
    memcpy(buf, cs + start, new_len);
    buf[new_len] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline KolStr kol_str_format(const char* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    va_list args_copy;
    va_copy(args_copy, args);
    int size = vsnprintf(NULL, 0, fmt, args_copy);
    va_end(args_copy);
    if (size < 0) {
        va_end(args);
        return kol_str_create("");
    }
    char* buf = (char*)malloc(size + 1);
    vsnprintf(buf, size + 1, fmt, args);
    va_end(args);
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

/* ============================================================================
 * OPTION AND RESULT TYPES
 * ============================================================================ */

typedef struct {
    bool has_value;
    union {
        int64_t val_int;
        double val_float;
        bool val_bool;
        KolStr val_str;
        void* val_ptr;
    };
} KolOption;

static inline KolOption kol_option_some_int(int64_t v) { KolOption o; o.has_value = true; o.val_int = v; return o; }
static inline KolOption kol_option_some_str(KolStr v) { KolOption o; o.has_value = true; o.val_str = v; return o; }
static inline KolOption kol_option_none(void) { KolOption o; o.has_value = false; return o; }

typedef struct {
    bool has_value;
    int64_t value;
} KolOptInt;

typedef struct {
    bool has_value;
    KolStr value;
} KolOptStr;

typedef struct {
    bool has_value;
    double value;
} KolOptFloat;

static inline KolOptInt kol_opt_int_some(int64_t v) {
    return (KolOptInt){true, v};
}
static inline KolOptInt kol_opt_int_none(void) {
    return (KolOptInt){false, 0};
}
static inline KolOptStr kol_opt_str_some(KolStr v) {
    return (KolOptStr){true, v};
}
static inline KolOptStr kol_opt_str_none(void) {
    KolStr s = kol_str_create("");
    return (KolOptStr){false, s};
}
static inline KolOptFloat kol_opt_float_some(double v) {
    return (KolOptFloat){true, v};
}
static inline KolOptFloat kol_opt_float_none(void) {
    return (KolOptFloat){false, 0.0};
}

typedef struct {
    bool ok;
    union {
        int64_t val_int;
        double val_float;
        bool val_bool;
        KolStr val_str;
        void* val_ptr;
    };
    KolStr err_msg;
} KolResult;

static inline KolResult kol_result_ok_int(int64_t v) { KolResult r; r.ok = true; r.val_int = v; return r; }
static inline KolResult kol_result_ok_float(double v) { KolResult r; r.ok = true; r.val_float = v; return r; }
static inline KolResult kol_result_ok_bool(bool v) { KolResult r; r.ok = true; r.val_bool = v; return r; }
static inline KolResult kol_result_ok_str(KolStr v) { KolResult r; r.ok = true; r.val_str = v; return r; }
static inline KolResult kol_result_err(KolStr err) { KolResult r; r.ok = false; r.err_msg = err; return r; }

/* ============================================================================
 * ARENA ALLOCATOR
 * ============================================================================ */

typedef struct KolArenaBlock {
    struct KolArenaBlock* next;
    size_t capacity;
    size_t used;
    char data[];
} KolArenaBlock;

typedef struct {
    KolArenaBlock* head;
    size_t default_capacity;
} KolArena;

static inline void kol_arena_init(KolArena* arena, size_t default_capacity) {
    arena->head = NULL;
    arena->default_capacity = default_capacity > 0 ? default_capacity : 4096;
}

static inline void* kol_arena_alloc(KolArena* arena, size_t size) {
    size = (size + 7) & ~7;
    if (!arena->head || (arena->head->used + size > arena->head->capacity)) {
        size_t cap = size > arena->default_capacity ? size : arena->default_capacity;
        KolArenaBlock* block = (KolArenaBlock*)malloc(sizeof(KolArenaBlock) + cap);
        if (!block) {
            fprintf(stderr, "Out of memory in kol_arena_alloc\n");
            exit(1);
        }
        block->capacity = cap;
        block->used = 0;
        block->next = arena->head;
        arena->head = block;
    }
    void* ptr = arena->head->data + arena->head->used;
    arena->head->used += size;
    return ptr;
}

static inline void kol_arena_free(KolArena* arena) {
    KolArenaBlock* curr = arena->head;
    while (curr) {
        KolArenaBlock* next = curr->next;
        free(curr);
        curr = next;
    }
    arena->head = NULL;
}

/* ============================================================================
 * DYNAMIC ARRAY (kol_array_t)
 * ============================================================================ */

typedef struct {
    void* data;
    size_t len;
    size_t capacity;
    size_t elem_size;
} kol_array_t;

/* Array results for `fn f() -> [T]!`.  KolResult is declared before kol_array_t,
 * so the array travels in a small heap box that take_arr unwraps and frees. */
static inline KolResult kol_result_ok_arr(kol_array_t v) {
    KolResult r;
    r.ok = true;
    kol_array_t* box = (kol_array_t*)malloc(sizeof(kol_array_t));
    *box = v;
    r.val_ptr = box;
    return r;
}
static inline kol_array_t kol_result_take_arr(KolResult r) {
    kol_array_t* box = (kol_array_t*)r.val_ptr;
    kol_array_t v = *box;
    free(box);
    return v;
}

static inline kol_array_t kol_array_create(size_t elem_size, size_t initial_capacity) {
    kol_array_t arr;
    arr.elem_size = elem_size;
    arr.len = 0;
    arr.capacity = initial_capacity;
    if (initial_capacity > 0) {
        arr.data = malloc(elem_size * initial_capacity);
    } else {
        arr.data = NULL;
    }
    return arr;
}

static inline void kol_array_push(kol_array_t* arr, const void* elem) {
    if (arr->len >= arr->capacity) {
        size_t new_cap = arr->capacity == 0 ? 4 : arr->capacity * 2;
        arr->data = realloc(arr->data, arr->elem_size * new_cap);
        arr->capacity = new_cap;
    }
    memcpy((char*)arr->data + (arr->len * arr->elem_size), elem, arr->elem_size);
    arr->len++;
}

static inline void* kol_array_get(const kol_array_t* arr, size_t index, const char* file, int line) {
    if (index >= arr->len) {
        kol_panic(file, line, "Array index out of bounds");
    }
    return (char*)arr->data + (index * arr->elem_size);
}

static inline void kol_array_free(kol_array_t* arr) {
    if (arr->data) {
        free(arr->data);
        arr->data = NULL;
    }
    arr->len = 0;
    arr->capacity = 0;
}

static inline void kol_array_free_strs(kol_array_t* arr) {
    if (arr->data) {
        KolStr* strs = (KolStr*)arr->data;
        for (size_t i = 0; i < arr->len; i++) {
            kol_arc_release_str(strs[i]);
        }
        free(arr->data);
        arr->data = NULL;
    }
    arr->len = 0;
    arr->capacity = 0;
}

/* Deep-ish copies used when a list crosses a task boundary (share-nothing). */
static inline kol_array_t kol_array_clone(const kol_array_t* a) {
    kol_array_t c = kol_array_create(a->elem_size, a->len);
    if (a->len) memcpy(c.data, a->data, a->len * a->elem_size);
    c.len = a->len;
    return c;
}

static inline kol_array_t kol_array_clone_strs(const kol_array_t* a) {
    kol_array_t c = kol_array_clone(a);
    KolStr* strs = (KolStr*)c.data;
    for (size_t i = 0; i < c.len; i++) kol_arc_retain_str(strs[i]);
    return c;
}

/* Deep-copy/free helpers for nested arrays of scalar values. depth counts
 * array layers; leaf_kind=1 means the innermost elements are refcounted KolStr. */
static inline kol_array_t kol_array_clone_deep(const kol_array_t* a, int depth, int leaf_kind) {
    if (depth <= 1) return leaf_kind ? kol_array_clone_strs(a) : kol_array_clone(a);
    kol_array_t c = kol_array_create(sizeof(kol_array_t), a->len);
    const kol_array_t* src = (const kol_array_t*)a->data;
    for (size_t i = 0; i < a->len; i++) {
        kol_array_t child = kol_array_clone_deep(&src[i], depth - 1, leaf_kind);
        kol_array_push(&c, &child);
    }
    return c;
}

static inline void kol_array_free_deep(kol_array_t* a, int depth, int leaf_kind) {
    if (depth <= 1) {
        if (leaf_kind) kol_array_free_strs(a);
        else kol_array_free(a);
        return;
    }
    if (a->data) {
        kol_array_t* children = (kol_array_t*)a->data;
        for (size_t i = 0; i < a->len; i++) kol_array_free_deep(&children[i], depth - 1, leaf_kind);
        free(a->data);
        a->data = NULL;
    }
    a->len = 0;
    a->capacity = 0;
}

static kol_array_t kol_program_args;
static inline void kol_runtime_set_args(int argc, char** argv) {
    kol_array_free_strs(&kol_program_args);
    kol_program_args = kol_array_create(sizeof(KolStr), argc > 1 ? (size_t)(argc-1) : 0);
    for (int i=1; i<argc; i++) { KolStr a=kol_str_create(argv[i]); kol_array_push(&kol_program_args, &a); }
}
static inline kol_array_t kol_runtime_args(void) { return kol_array_clone_strs(&kol_program_args); }
static inline int64_t kol_runtime_arg_count(void) { return (int64_t)kol_program_args.len; }
static inline KolStr kol_runtime_arg_at(int64_t i) { if(i<0 || (size_t)i>=kol_program_args.len)return kol_str_create(""); return kol_str_create(kol_str_cstr(&((KolStr*)kol_program_args.data)[i])); }

/* Lists whose elements are refcounted handles (channels, futures, ...). */
static inline void kol_array_free_objs(kol_array_t* arr) {
    if (arr->data) {
        void** objs = (void**)arr->data;
        for (size_t i = 0; i < arr->len; i++) kol_obj_release(objs[i]);
        free(arr->data);
        arr->data = NULL;
    }
    arr->len = 0;
    arr->capacity = 0;
}

static inline void kol_array_retain_objs(const kol_array_t* arr) {
    void** objs = (void**)arr->data;
    for (size_t i = 0; i < arr->len; i++) kol_obj_retain(objs[i]);
}

static inline void kol_array_pop(kol_array_t* a) {
    if (a->len > 0) {
        a->len--;
    }
}

/* pop that hands the removed element back (strings: ownership moves to caller) */
static inline int64_t kol_array_pop_int(kol_array_t* a) {
    if (a->len == 0) return 0;
    a->len--;
    return ((int64_t*)a->data)[a->len];
}
static inline double kol_array_pop_float(kol_array_t* a) {
    if (a->len == 0) return 0.0;
    a->len--;
    return ((double*)a->data)[a->len];
}
static inline bool kol_array_pop_bool(kol_array_t* a) {
    if (a->len == 0) return false;
    a->len--;
    return ((bool*)a->data)[a->len];
}
static inline KolStr kol_array_pop_str(kol_array_t* a) {
    if (a->len == 0) return kol_str_create("");
    a->len--;
    return ((KolStr*)a->data)[a->len];
}

/* copy of a list of refcounted handles: every element gains a reference */
static inline kol_array_t kol_array_clone_objs(const kol_array_t* a) {
    kol_array_t c = kol_array_clone(a);
    kol_array_retain_objs(&c);
    return c;
}

/* replace a [str] element, releasing the one it overwrites */
static inline void kol_array_set_str(kol_array_t* a, size_t i, KolStr v, const char* file, int line) {
    if (i >= a->len) kol_panic(file, line, "Array index out of bounds");
    KolStr* strs = (KolStr*)a->data;
    kol_arc_release_str(strs[i]);
    strs[i] = v;
}

static inline void kol_array_reverse(kol_array_t* a) {
    if (a->len <= 1 || !a->data) return;
    size_t i = 0;
    size_t j = a->len - 1;
    char* buf = (char*)malloc(a->elem_size);
    while (i < j) {
        char* pi = (char*)a->data + (i * a->elem_size);
        char* pj = (char*)a->data + (j * a->elem_size);
        memcpy(buf, pi, a->elem_size);
        memcpy(pi, pj, a->elem_size);
        memcpy(pj, buf, a->elem_size);
        i++;
        j--;
    }
    free(buf);
}

static inline bool kol_array_contains_int(kol_array_t* a, int64_t val) {
    for (size_t i = 0; i < a->len; i++) {
        int64_t v = *((int64_t*)((char*)a->data + (i * a->elem_size)));
        if (v == val) return true;
    }
    return false;
}

static inline kol_array_t kol_str_split(KolStr s, KolStr sep) {
    kol_array_t arr = kol_array_create(sizeof(KolStr), 4);
    const char* cs = kol_str_cstr(&s);
    const char* csep = kol_str_cstr(&sep);
    size_t sep_len = kol_str_len(sep);

    if (sep_len == 0) {
        KolStr item = kol_str_create(cs);
        kol_array_push(&arr, &item);
        return arr;
    }

    const char* cur = cs;
    const char* next;
    while ((next = strstr(cur, csep)) != NULL) {
        size_t part_len = next - cur;
        char* buf = (char*)malloc(part_len + 1);
        memcpy(buf, cur, part_len);
        buf[part_len] = '\0';
        KolStr part = kol_str_create(buf);
        free(buf);
        kol_array_push(&arr, &part);
        cur = next + sep_len;
    }

    KolStr rest = kol_str_create(cur);
    kol_array_push(&arr, &rest);
    return arr;
}

static inline bool kol_array_contains_str(kol_array_t* a, KolStr val) {
    for (size_t i = 0; i < a->len; i++) {
        KolStr v = *((KolStr*)((char*)a->data + (i * a->elem_size)));
        if (kol_str_eq(v, val)) return true;
    }
    return false;
}

/* ============================================================================
 * HASH MAP (KolMap_si)
 * ============================================================================ */

typedef struct KolMapEntry_si {
    KolStr key;
    int64_t val;
    struct KolMapEntry_si* next;
} KolMapEntry_si;

typedef struct {
    KolMapEntry_si** buckets;
    size_t num_buckets;
    size_t count;
} KolMap_si;

static inline KolMap_si kol_map_si_create(size_t num_buckets) {
    KolMap_si m;
    m.num_buckets = num_buckets > 0 ? num_buckets : 16;
    m.buckets = (KolMapEntry_si**)calloc(m.num_buckets, sizeof(KolMapEntry_si*));
    m.count = 0;
    return m;
}

static inline uint32_t kol_hash_str(KolStr s) {
    const char* str = kol_str_cstr(&s);
    uint32_t hash = 5381;
    int c;
    while ((c = *str++))
        hash = ((hash << 5) + hash) + c;
    return hash;
}

static inline void kol_map_si_set(KolMap_si* m, KolStr key, int64_t val) {
    uint32_t hash = kol_hash_str(key);
    size_t bucket = hash % m->num_buckets;
    KolMapEntry_si* e = m->buckets[bucket];
    while (e) {
        if (kol_str_eq(e->key, key)) {
            e->val = val;
            return;
        }
        e = e->next;
    }
    KolMapEntry_si* new_e = (KolMapEntry_si*)malloc(sizeof(KolMapEntry_si));
    new_e->key = key;
    new_e->val = val;
    new_e->next = m->buckets[bucket];
    m->buckets[bucket] = new_e;
    m->count++;
}

static inline int64_t kol_map_si_get(KolMap_si* m, KolStr key) {
    uint32_t hash = kol_hash_str(key);
    size_t bucket = hash % m->num_buckets;
    KolMapEntry_si* e = m->buckets[bucket];
    while (e) {
        if (kol_str_eq(e->key, key)) {
            return e->val;
        }
        e = e->next;
    }
    return 0;
}

/* ============================================================================
 * CONCURRENCY (tasks, futures, channels, select, mutex, atomics)
 * Only compiled in when the program uses it (the compiler defines
 * KOL_USE_CONCURRENCY), so single-threaded programs pay nothing.
 * ============================================================================ */

#ifdef KOL_USE_CONCURRENCY
#include "kol_conc.h"
#endif

/* ============================================================================
 * SYSTEM MEMORY HELPERS
 * ============================================================================ */

static inline void* kol_mem_alloc(size_t n) { return malloc(n); }
static inline void kol_mem_free(void* p) { free(p); }
static inline void* kol_mem_realloc(void* p, size_t n) { return realloc(p, n); }
static inline void kol_mem_set(void* p, int v, size_t n) { memset(p, v, n); }
static inline void kol_mem_copy(void* d, const void* s, size_t n) { memcpy(d, s, n); }

/* ============================================================================
 * PRINT HELPERS
 * ============================================================================ */

static inline void kol_print_int(int64_t val) {
    printf("%lld\n", (long long)val);
}

static inline void kol_print_float(double val) {
    printf("%g\n", val);
}

static inline void kol_print_bool(bool val) {
    printf("%s\n", val ? "true" : "false");
}

static inline void kol_print_str(KolStr s) {
    printf("%s\n", kol_str_cstr(&s));
}

static inline void kol_print_none(void) {
    printf("none\n");
}


/* ============================================================================
 * STANDARD LIBRARY SUPPORT
 * Small, self-contained helpers used by the std .kol modules through `extern fn`.
 * All of them take/return plain Kol values (int, float, bool, str) so they can
 * be bound with `extern fn name(...) -> T from "kol_runtime"`.
 * ============================================================================ */

#include <errno.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>

/* ---- characters --------------------------------------------------------- */

static inline int64_t kol_ord(KolStr s) {
    if (kol_str_len(s) == 0) return -1;
    return (int64_t)(unsigned char)kol_str_cstr(&s)[0];
}

static inline KolStr kol_chr(int64_t code) {
    char buf[2];
    buf[0] = (char)(code & 0xFF);
    buf[1] = '\0';
    if (code <= 0 || code > 255) buf[0] = '\0';
    return kol_str_create(buf);
}

/* ord of the character at byte index i, or -1 when out of range */
static inline int64_t kol_char_code_at(KolStr s, int64_t i) {
    if (i < 0 || (size_t)i >= kol_str_len(s)) return -1;
    return (int64_t)(unsigned char)kol_str_cstr(&s)[i];
}

/* first index of sub in s at or after `from`, or -1 */
static inline int64_t kol_str_find(KolStr s, KolStr sub, int64_t from) {
    size_t ls = kol_str_len(s), lsub = kol_str_len(sub);
    if (from < 0) from = 0;
    if ((size_t)from > ls) return -1;
    if (lsub == 0) return from;
    const char* cs = kol_str_cstr(&s);
    const char* hit = strstr(cs + from, kol_str_cstr(&sub));
    if (!hit) return -1;
    return (int64_t)(hit - cs);
}

/* Last index of sub in s, or -1. Uses byte offsets like slice/find. */
static inline int64_t kol_str_rfind(KolStr s, KolStr sub) {
    size_t ls=kol_str_len(s), ln=kol_str_len(sub); if(ln==0)return (int64_t)ls; if(ln>ls)return -1;
    const char* text=kol_str_cstr(&s); const char* needle=kol_str_cstr(&sub); size_t i=ls-ln+1;
    while(i>0){i--;if(text[i]==needle[0] && memcmp(text+i,needle,ln)==0)return (int64_t)i;}return -1;
}

/* ---- number parsing ----------------------------------------------------- */

/* 1 when the whole string is a valid float literal, else 0 */
static inline bool kol_str_is_float(KolStr s) {
    const char* cs = kol_str_cstr(&s);
    if (*cs == '\0' || isspace((unsigned char)*cs)) return false;
    char* end = NULL;
    errno = 0;
    strtod(cs, &end);
    return end && *end == '\0' && errno == 0;
}

static inline double kol_str_to_float(KolStr s) {
    return strtod(kol_str_cstr(&s), NULL);
}

/* ---- file system -------------------------------------------------------- */

static inline bool kol_file_exists(KolStr path) {
    struct stat st;
    return stat(kol_str_cstr(&path), &st) == 0;
}

static inline bool kol_dir_exists(KolStr path) {
    struct stat st;
    return stat(kol_str_cstr(&path), &st) == 0 && S_ISDIR(st.st_mode);
}

static inline int64_t kol_file_size(KolStr path) {
    struct stat st;
    if (stat(kol_str_cstr(&path), &st) != 0) return -1;
    return (int64_t)st.st_size;
}

/* whole file as a string; empty string when unreadable (check kol_file_exists) */
static inline KolStr kol_file_read(KolStr path) {
    FILE* f = fopen(kol_str_cstr(&path), "rb");
    if (!f) return kol_str_create("");
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return kol_str_create(""); }
    char* buf = (char*)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return kol_str_create(""); }
    size_t got = fread(buf, 1, (size_t)n, f);
    buf[got] = '\0';
    fclose(f);
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline bool kol_file_write_mode(KolStr path, KolStr data, const char* mode) {
    FILE* f = fopen(kol_str_cstr(&path), mode);
    if (!f) return false;
    size_t n = kol_str_len(data);
    size_t put = n ? fwrite(kol_str_cstr(&data), 1, n, f) : 0;
    int rc = fclose(f);
    return put == n && rc == 0;
}

static inline bool kol_file_write(KolStr path, KolStr data)  { return kol_file_write_mode(path, data, "wb"); }
static inline bool kol_file_append(KolStr path, KolStr data) { return kol_file_write_mode(path, data, "ab"); }
static inline bool kol_file_remove(KolStr path) { return remove(kol_str_cstr(&path)) == 0; }
static inline bool kol_file_rename(KolStr from, KolStr to) { return rename(kol_str_cstr(&from), kol_str_cstr(&to)) == 0; }
static inline bool kol_dir_make(KolStr path) { return mkdir(kol_str_cstr(&path), 0777) == 0; }

/* ---- environment / process --------------------------------------------- */

static inline bool kol_env_has(KolStr name) { return getenv(kol_str_cstr(&name)) != NULL; }

static inline KolStr kol_env_get(KolStr name) {
    const char* v = getenv(kol_str_cstr(&name));
    return kol_str_create(v ? v : "");
}

static inline KolStr kol_cwd(void) {
    char buf[4096];
    if (!getcwd(buf, sizeof(buf))) return kol_str_create("");
    return kol_str_create(buf);
}

static inline void kol_exit(int64_t code) { fflush(stdout); exit((int)code); }

/* ---- time --------------------------------------------------------------- */

static inline int64_t kol_time_unix(void) { return (int64_t)time(NULL); }

static inline int64_t kol_time_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + (int64_t)(ts.tv_nsec / 1000000);
}

static inline int64_t kol_time_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
}

/* sleep; inside a concurrent program this defers to the scheduler-aware
 * kol_sleep_ms from kol_conc.h so a sleeping task does not starve the pool */
static inline void kol_time_sleep_ms(int64_t ms) {
#ifdef KOL_USE_CONCURRENCY
    kol_sleep_ms(ms);
#else
    if (ms <= 0) return;
    struct timespec ts;
    ts.tv_sec = (time_t)(ms / 1000);
    ts.tv_nsec = (long)((ms % 1000) * 1000000L);
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {}
#endif
}

/* broken-down UTC time for a unix timestamp. field: 0=year 1=month 2=day
 * 3=hour 4=minute 5=second 6=weekday(0=Sunday) 7=day-of-year(1-based) */
static inline int64_t kol_time_field(int64_t unix_ts, int64_t field) {
    time_t t = (time_t)unix_ts;
    struct tm tmv;
    if (!gmtime_r(&t, &tmv)) return -1;
    switch (field) {
        case 0: return tmv.tm_year + 1900;
        case 1: return tmv.tm_mon + 1;
        case 2: return tmv.tm_mday;
        case 3: return tmv.tm_hour;
        case 4: return tmv.tm_min;
        case 5: return tmv.tm_sec;
        case 6: return tmv.tm_wday;
        case 7: return tmv.tm_yday + 1;
    }
    return -1;
}

/* ---- random (xorshift64*, per-thread state) ----------------------------- */

static _Thread_local uint64_t kol_rng_state = 0x9E3779B97F4A7C15ULL;

static inline void kol_rand_seed(int64_t seed) {
    uint64_t s = (uint64_t)seed;
    s += 0x9E3779B97F4A7C15ULL;
    s = (s ^ (s >> 30)) * 0xBF58476D1CE4E5B9ULL;
    s = (s ^ (s >> 27)) * 0x94D049BB133111EBULL;
    s ^= s >> 31;
    kol_rng_state = s ? s : 0x9E3779B97F4A7C15ULL;
}

/* non-negative 62-bit pseudo random integer */
static inline int64_t kol_rand_next(void) {
    uint64_t x = kol_rng_state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    kol_rng_state = x;
    return (int64_t)((x * 0x2545F4914F6CDD1DULL) >> 2);
}

/* ---- console input / output -------------------------------------------- */

static inline void kol_write(KolStr s)  { fputs(kol_str_cstr(&s), stdout); }
static inline void kol_ewrite(KolStr s) { fputs(kol_str_cstr(&s), stderr); }
static inline void kol_eprint_line(KolStr s) { fprintf(stderr, "%s\n", kol_str_cstr(&s)); }
static inline void kol_flush(void) { fflush(stdout); }

static _Thread_local bool kol_stdin_at_eof = false;

/* next line of stdin without the trailing newline; sets the EOF flag at end */
static inline KolStr kol_read_line(void) {
    size_t cap = 128, len = 0;
    char* buf = (char*)malloc(cap);
    int c;
    bool got_any = false;
    while ((c = fgetc(stdin)) != EOF) {
        got_any = true;
        if (c == '\n') break;
        if (len + 2 > cap) { cap *= 2; buf = (char*)realloc(buf, cap); }
        buf[len++] = (char)c;
    }
    if (c == EOF && !got_any) kol_stdin_at_eof = true;
    if (len > 0 && buf[len - 1] == '\r') len--;
    buf[len] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
}

static inline bool kol_stdin_eof(void) { return kol_stdin_at_eof; }


/* ---- bit operations (the parser has no bitwise operators yet) ----------- */

static inline int64_t kol_bit_and(int64_t a, int64_t b) { return a & b; }
static inline int64_t kol_bit_or(int64_t a, int64_t b)  { return a | b; }
static inline int64_t kol_bit_xor(int64_t a, int64_t b) { return a ^ b; }
static inline int64_t kol_bit_not(int64_t a) { return ~a; }
static inline int64_t kol_bit_shl(int64_t a, int64_t n) {
    if (n < 0 || n > 63) return 0;
    return (int64_t)((uint64_t)a << n);
}
/* arithmetic shift right */
static inline int64_t kol_bit_shr(int64_t a, int64_t n) {
    if (n < 0) return 0;
    if (n > 63) return a < 0 ? -1 : 0;
    return a >> n;
}
static inline int64_t kol_bit_popcount(int64_t a) {
    uint64_t x = (uint64_t)a;
    int64_t c = 0;
    while (x) { x &= x - 1; c++; }
    return c;
}

/* ---- hashing ------------------------------------------------------------ */

/* FNV-1a, folded to a non-negative 62-bit int */
static inline int64_t kol_hash_fnv1a(KolStr s) {
    const unsigned char* p = (const unsigned char*)kol_str_cstr(&s);
    uint64_t h = 1469598103934665603ULL;
    while (*p) { h ^= *p++; h *= 1099511628211ULL; }
    return (int64_t)(h >> 2);
}

static inline int64_t kol_hash_djb2(KolStr s) {
    const unsigned char* p = (const unsigned char*)kol_str_cstr(&s);
    uint64_t h = 5381;
    while (*p) h = ((h << 5) + h) + *p++;
    return (int64_t)(h >> 2);
}

static inline int64_t kol_hash_crc32(KolStr s) {
    const unsigned char* p = (const unsigned char*)kol_str_cstr(&s);
    uint32_t crc = 0xFFFFFFFFu;
    while (*p) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1)));
    }
    return (int64_t)(crc ^ 0xFFFFFFFFu);
}


/* ==========================================================================
 * JSON and regular-expression helpers used by std.json and std.regex.
 * JSON parsing is bounded by a nesting limit to avoid stack exhaustion.
 * Regex uses the platform POSIX ERE engine where available.
 * ========================================================================== */
static inline const char* kol_json_ws(const char* p) {
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    return p;
}
static inline const char* kol_json_string_end(const char* p) {
    if (*p != '"') return NULL;
    p++;
    while (*p) {
        unsigned char c = (unsigned char)*p++;
        if (c == '"') return p;
        if (c < 0x20) return NULL;
        if (c == '\\') {
            if (!*p) return NULL;
            char e = *p++;
            if (e == 'u') {
                for (int i = 0; i < 4; i++) {
                    char h = *p++;
                    if (!h || !isxdigit((unsigned char)h)) return NULL;
                }
            } else if (!(e == '"' || e == '\\' || e == '/' || e == 'b' || e == 'f' || e == 'n' || e == 'r' || e == 't')) return NULL;
        }
    }
    return NULL;
}
static inline const char* kol_json_value_end(const char* p, int depth) {
    if (depth > 256) return NULL;
    p = kol_json_ws(p);
    if (*p == '"') return kol_json_string_end(p);
    if (*p == '{') {
        p = kol_json_ws(p + 1);
        if (*p == '}') return p + 1;
        while (*p) {
            p = kol_json_ws(p);
            const char* q = kol_json_string_end(p); if (!q) return NULL;
            p = kol_json_ws(q);
            if (*p++ != ':') return NULL;
            q = kol_json_value_end(p, depth + 1); if (!q) return NULL;
            p = kol_json_ws(q);
            if (*p == '}') return p + 1;
            if (*p++ != ',') return NULL;
        }
        return NULL;
    }
    if (*p == '[') {
        p = kol_json_ws(p + 1);
        if (*p == ']') return p + 1;
        while (*p) {
            const char* q = kol_json_value_end(p, depth + 1); if (!q) return NULL;
            p = kol_json_ws(q);
            if (*p == ']') return p + 1;
            if (*p++ != ',') return NULL;
        }
        return NULL;
    }
    if (!strncmp(p, "true", 4)) return p + 4;
    if (!strncmp(p, "false", 5)) return p + 5;
    if (!strncmp(p, "null", 4)) return p + 4;
    const char* n = p;
    if (*p == '-') p++;
    if (*p == '0') p++;
    else { if (!isdigit((unsigned char)*p)) return NULL; while (isdigit((unsigned char)*p)) p++; }
    if (*p == '.') { p++; if (!isdigit((unsigned char)*p)) return NULL; while (isdigit((unsigned char)*p)) p++; }
    if (*p == 'e' || *p == 'E') { p++; if (*p == '+' || *p == '-') p++; if (!isdigit((unsigned char)*p)) return NULL; while (isdigit((unsigned char)*p)) p++; }
    return p == n ? NULL : p;
}
static inline bool kol_json_is_valid(KolStr json) {
    const char* p = kol_json_ws(kol_str_cstr(&json));
    const char* end = kol_json_value_end(p, 0);
    return end && *kol_json_ws(end) == '\0';
}
static inline KolStr kol_json_quote(KolStr value) {
    const unsigned char* p = (const unsigned char*)kol_str_cstr(&value);
    size_t n = strlen((const char*)p), cap = n * 6 + 3, len = 0;
    char* out = (char*)malloc(cap); if (!out) return kol_str_create("\"\"");
    out[len++] = '"';
    for (size_t i = 0; i < n; i++) {
        unsigned char c = p[i];
        if (c == '"' || c == '\\') { out[len++] = '\\'; out[len++] = (char)c; }
        else if (c == '\n') { out[len++]='\\'; out[len++]='n'; }
        else if (c == '\r') { out[len++]='\\'; out[len++]='r'; }
        else if (c == '\t') { out[len++]='\\'; out[len++]='t'; }
        else if (c == '\b') { out[len++]='\\'; out[len++]='b'; }
        else if (c == '\f') { out[len++]='\\'; out[len++]='f'; }
        else if (c < 0x20) { snprintf(out + len, 7, "\\u%04x", c); len += 6; }
        else out[len++] = (char)c;
    }
    out[len++] = '"'; out[len] = '\0';
    KolStr result = kol_str_create(out); free(out); return result;
}
static inline KolStr kol_json_get_raw(KolStr json, KolStr key) {
    const char* p = kol_json_ws(kol_str_cstr(&json));
    if (*p++ != '{') return kol_str_create("");
    const char* wanted = kol_str_cstr(&key);
    p = kol_json_ws(p);
    while (*p && *p != '}') {
        const char* ks = p; const char* ke = kol_json_string_end(p); if (!ke) return kol_str_create("");
        size_t kl = (size_t)(ke - ks - 2); char* k = (char*)malloc(kl + 1); if (!k) return kol_str_create("");
        memcpy(k, ks + 1, kl); k[kl] = 0;
        p = kol_json_ws(ke); if (*p++ != ':') { free(k); return kol_str_create(""); }
        p = kol_json_ws(p); const char* ve = kol_json_value_end(p, 0); if (!ve) { free(k); return kol_str_create(""); }
        bool match = strcmp(k, wanted) == 0; free(k);
        if (match) { size_t n = (size_t)(ve-p); char* raw=(char*)malloc(n+1); if(!raw)return kol_str_create(""); memcpy(raw,p,n);raw[n]=0;KolStr r=kol_str_create(raw);free(raw);return r; }
        p = kol_json_ws(ve); if (*p == ',') p = kol_json_ws(p + 1); else break;
    }
    return kol_str_create("");
}
static inline KolStr kol_json_get_string(KolStr json, KolStr key) {
    KolStr raw = kol_json_get_raw(json, key); const char* p = kol_str_cstr(&raw);
    if (*p != '"') { kol_arc_release_str(raw); return kol_str_create(""); }
    size_t n = strlen(p); char* out=(char*)malloc(n+1); if(!out){kol_arc_release_str(raw);return kol_str_create("");}
    size_t j=0;
    for (size_t i=1; i+1<n; i++) {
        char c=p[i];
        if(c=='\\' && i+1<n-1) { char e=p[++i]; if(e=='n')c='\n'; else if(e=='r')c='\r'; else if(e=='t')c='\t'; else if(e=='b')c='\b'; else if(e=='f')c='\f'; else if(e=='u') { out[j++]='\\';out[j++]='u'; for(int z=0;z<4&&i+1<n-1;z++)out[j++]=p[++i]; continue; } else c=e; }
        out[j++]=c;
    }
    out[j]=0; KolStr result=kol_str_create(out); free(out); kol_arc_release_str(raw); return result;
}
static inline int64_t kol_json_array_len(KolStr json) {
    const char* p=kol_json_ws(kol_str_cstr(&json)); if(*p++!='[')return -1; p=kol_json_ws(p); if(*p==']')return 0;
    int64_t n=0; while(*p){const char* e=kol_json_value_end(p,0);if(!e)return -1;n++;p=kol_json_ws(e);if(*p==']')return n;if(*p++!=',')return -1;p=kol_json_ws(p);}return -1;
}
static inline KolStr kol_json_array_get_raw(KolStr json, int64_t index) {
    if(index<0)return kol_str_create("");const char* p=kol_json_ws(kol_str_cstr(&json));if(*p++!='[')return kol_str_create("");p=kol_json_ws(p);int64_t i=0;
    while(*p&&*p!=']'){const char* e=kol_json_value_end(p,0);if(!e)return kol_str_create("");if(i==index){size_t n=(size_t)(e-p);char* b=(char*)malloc(n+1);if(!b)return kol_str_create("");memcpy(b,p,n);b[n]=0;KolStr r=kol_str_create(b);free(b);return r;}i++;p=kol_json_ws(e);if(*p==',')p=kol_json_ws(p+1);else break;}return kol_str_create("");
}
#ifndef _WIN32
static inline bool kol_regex_is_match(KolStr pattern, KolStr input, bool insensitive) {
    regex_t re; int flags=REG_EXTENDED | (insensitive ? REG_ICASE : 0);
    if(regcomp(&re, kol_str_cstr(&pattern), flags)!=0)return false;
    int rc=regexec(&re, kol_str_cstr(&input), 0, NULL, 0); regfree(&re); return rc==0;
}
static inline KolStr kol_regex_find(KolStr pattern, KolStr input, bool insensitive) {
    regex_t re; regmatch_t m; int flags=REG_EXTENDED | (insensitive ? REG_ICASE : 0);
    if(regcomp(&re, kol_str_cstr(&pattern), flags)!=0)return kol_str_create("");
    int rc=regexec(&re, kol_str_cstr(&input), 1, &m, 0); regfree(&re);
    if(rc || m.rm_so<0 || m.rm_eo<m.rm_so)return kol_str_create("");
    const char* s=kol_str_cstr(&input); size_t n=(size_t)(m.rm_eo-m.rm_so); char* b=(char*)malloc(n+1);if(!b)return kol_str_create("");memcpy(b,s+m.rm_so,n);b[n]=0;KolStr r=kol_str_create(b);free(b);return r;
}
static inline KolStr kol_regex_replace_all(KolStr pattern, KolStr input, KolStr replacement, bool insensitive) {
    regex_t re; int flags=REG_EXTENDED | (insensitive ? REG_ICASE : 0);
    if(regcomp(&re, kol_str_cstr(&pattern), flags)!=0)return kol_str_create(kol_str_cstr(&input));
    const char* src=kol_str_cstr(&input); const char* rep=kol_str_cstr(&replacement); size_t cap=strlen(src)+1, len=0; char* out=(char*)malloc(cap); if(!out){regfree(&re);return kol_str_create("");} out[0]=0;
    size_t loops=0;
    while(*src && loops++ < 100000) {
        regmatch_t m; int rc=regexec(&re,src,1,&m,0);
        if(rc || m.rm_so<0) { size_t n=strlen(src); if(len+n+1>cap){cap=len+n+1;out=realloc(out,cap);}memcpy(out+len,src,n);len+=n;break; }
        size_t prefix=(size_t)m.rm_so, match=(size_t)m.rm_eo;
        if(len+prefix+strlen(rep)+1>cap){cap=(len+prefix+strlen(rep)+1)*2;out=realloc(out,cap);}memcpy(out+len,src,prefix);len+=prefix;memcpy(out+len,rep,strlen(rep));len+=strlen(rep);
        if(match==0) { if(*src){if(len+2>cap){cap*=2;out=realloc(out,cap);}out[len++]=*src++;} else break; } else src+=match;
    }
    out[len]=0; regfree(&re); KolStr r=kol_str_create(out);free(out);return r;
}
#else
static inline bool kol_regex_is_match(KolStr p, KolStr s, bool i) { (void)p;(void)s;(void)i;return false; }
static inline KolStr kol_regex_find(KolStr p, KolStr s, bool i) { (void)p;(void)s;(void)i;return kol_str_create(""); }
static inline KolStr kol_regex_replace_all(KolStr p, KolStr s, KolStr r, bool i) { (void)p;(void)r;(void)i;return kol_str_create(kol_str_cstr(&s)); }
#endif


/* ==========================================================================
 * Filesystem, process and networking primitives. These are deliberately
 * synchronous; async scheduling remains owned by the existing task runtime.
 * ========================================================================== */
static inline bool kol_fs_is_file(KolStr path) {
    struct stat st; return stat(kol_str_cstr(&path), &st) == 0 && S_ISREG(st.st_mode);
}
static inline bool kol_fs_is_dir(KolStr path) {
    struct stat st; return stat(kol_str_cstr(&path), &st) == 0 && S_ISDIR(st.st_mode);
}
static inline bool kol_fs_is_separator(char c) {
#ifdef _WIN32
    return c=='/' || c=='\\';
#else
    return c=='/';
#endif
}
static inline int64_t kol_fs_mkdirs(KolStr path) {
    const char* src=kol_str_cstr(&path); size_t n=strlen(src); if(n==0)return -1; char* b=(char*)malloc(n+1); if(!b)return -1; memcpy(b,src,n+1);
    for(size_t i=1;i<=n;i++) if(kol_fs_is_separator(b[i]) || b[i]=='\0') { char save=b[i]; b[i]=0; if(b[0]) { KolStr prefix=kol_str_create(b); bool is_dir=kol_fs_is_dir(prefix); kol_arc_release_str(prefix); if(!is_dir) {
#ifndef _WIN32
        if(mkdir(b,0777)!=0 && errno!=EEXIST) { free(b); return -1; }
#else
        if(_mkdir(b)!=0 && errno!=EEXIST) { free(b); return -1; }
#endif
        KolStr verify=kol_str_create(b); bool made=kol_fs_is_dir(verify); kol_arc_release_str(verify); if(!made) { free(b); return -1; }
    } } b[i]=save; }
    free(b); return 0;
}
static inline int64_t kol_process_status(KolStr command) {
    int status=system(kol_str_cstr(&command)); if(status==-1)return -1;
#ifndef _WIN32
    if(WIFEXITED(status))return (int64_t)WEXITSTATUS(status);
    if(WIFSIGNALED(status))return (int64_t)(128+WTERMSIG(status));
#endif
    return (int64_t)status;
}
static inline KolStr kol_process_output(KolStr command) {
#ifdef _WIN32
    FILE* f=_popen(kol_str_cstr(&command),"r");
#else
    FILE* f=popen(kol_str_cstr(&command),"r");
#endif
    if(!f)return kol_str_create(""); size_t cap=4096,len=0; char* b=(char*)malloc(cap); if(!b){
#ifdef _WIN32
    _pclose(f);
#else
    pclose(f);
#endif
    return kol_str_create(""); }
    size_t n; while((n=fread(b+len,1,cap-len-1,f))>0){len+=n;if(cap-len<2){cap*=2;char* t=(char*)realloc(b,cap);if(!t)break;b=t;}}
#ifdef _WIN32
    _pclose(f);
#else
    pclose(f);
#endif
    b[len]=0; KolStr r=kol_str_create(b);free(b);return r;
}
static inline kol_array_t kol_fs_list_dir(KolStr path) {
    kol_array_t out=kol_array_create(sizeof(KolStr),8);
#ifndef _WIN32
    DIR* d=opendir(kol_str_cstr(&path)); if(!d)return out; struct dirent* e;
    while((e=readdir(d))!=NULL){if(!strcmp(e->d_name,".")||!strcmp(e->d_name,".."))continue;KolStr v=kol_str_create(e->d_name);kol_array_push(&out,&v);}
    closedir(d);
#endif
    return out;
}
#ifndef _WIN32
static inline int64_t kol_net_connect(KolStr host, int64_t port, int64_t timeout_ms) {
    char service[16]; snprintf(service,sizeof(service),"%lld",(long long)port); struct addrinfo hints,*res=NULL,*it; memset(&hints,0,sizeof(hints));hints.ai_socktype=SOCK_STREAM;hints.ai_family=AF_UNSPEC;
    if(getaddrinfo(kol_str_cstr(&host),service,&hints,&res)!=0)return -1; int fd=-1;
    for(it=res;it;it=it->ai_next){fd=(int)socket(it->ai_family,it->ai_socktype,it->ai_protocol);if(fd<0)continue;
        int old_flags=fcntl(fd,F_GETFL,0);if(old_flags>=0)fcntl(fd,F_SETFL,old_flags|O_NONBLOCK);
        int rc=connect(fd,it->ai_addr,it->ai_addrlen);
        if(rc<0 && errno==EINPROGRESS){struct pollfd pfd;pfd.fd=fd;pfd.events=POLLOUT;pfd.revents=0;rc=poll(&pfd,1,timeout_ms>0?(int)timeout_ms:-1);if(rc>0){int err=0;socklen_t elen=sizeof(err);if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&err,&elen)!=0||err!=0)rc=-1;else rc=0;}else rc=-1;}
        if(old_flags>=0)fcntl(fd,F_SETFL,old_flags);
        if(rc==0){if(timeout_ms>0){struct timeval tv;tv.tv_sec=(time_t)(timeout_ms/1000);tv.tv_usec=(suseconds_t)((timeout_ms%1000)*1000);setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));}break;}close(fd);fd=-1;
    } freeaddrinfo(res);return fd;
}
static inline int64_t kol_net_listen(int64_t port, int64_t backlog) {
    int fd=socket(AF_INET6,SOCK_STREAM,0);if(fd<0)return -1;int yes=1;setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));int no=0;setsockopt(fd,IPPROTO_IPV6,IPV6_V6ONLY,&no,sizeof(no));
    struct sockaddr_in6 addr;memset(&addr,0,sizeof(addr));addr.sin6_family=AF_INET6;addr.sin6_addr=in6addr_any;addr.sin6_port=htons((uint16_t)port);
    if(bind(fd,(struct sockaddr*)&addr,sizeof(addr))!=0||listen(fd,(int)(backlog>0?backlog:16))!=0){close(fd);return -1;}return fd;
}
static inline int64_t kol_net_accept(int64_t fd) { return accept((int)fd,NULL,NULL); }
static inline int64_t kol_net_send(int64_t fd, KolStr data) { const char* p=kol_str_cstr(&data);size_t n=strlen(p),done=0;while(done<n){ssize_t r=send((int)fd,p+done,n-done,0);if(r<=0)return done?(int64_t)done:-1;done+=(size_t)r;}return (int64_t)done; }
static inline KolStr kol_net_recv(int64_t fd, int64_t max_bytes) { if(max_bytes<1)max_bytes=4096;if(max_bytes>1048576)max_bytes=1048576;char* b=(char*)malloc((size_t)max_bytes+1);if(!b)return kol_str_create("");ssize_t n=recv((int)fd,b,(size_t)max_bytes,0);if(n<0)n=0;b[n]=0;KolStr r=kol_str_create(b);free(b);return r; }
static inline void kol_net_close(int64_t fd) { if(fd>=0)close((int)fd); }
static inline KolStr kol_net_resolve(KolStr host) {
    struct addrinfo hints,*res=NULL;memset(&hints,0,sizeof(hints));hints.ai_family=AF_UNSPEC;hints.ai_socktype=SOCK_STREAM;
    if(getaddrinfo(kol_str_cstr(&host),NULL,&hints,&res)!=0)return kol_str_create("");char out[INET6_ADDRSTRLEN];out[0]=0;
    for(struct addrinfo* it=res;it;it=it->ai_next){void* addr=NULL;if(it->ai_family==AF_INET)addr=&((struct sockaddr_in*)it->ai_addr)->sin_addr;else if(it->ai_family==AF_INET6)addr=&((struct sockaddr_in6*)it->ai_addr)->sin6_addr;if(addr&&inet_ntop(it->ai_family,addr,out,sizeof(out)))break;}freeaddrinfo(res);return kol_str_create(out);
}
static inline int64_t kol_udp_bind(int64_t port) {
    int fd=socket(AF_INET,SOCK_DGRAM,0);if(fd<0)return -1;int yes=1;setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));struct sockaddr_in a;memset(&a,0,sizeof(a));a.sin_family=AF_INET;a.sin_addr.s_addr=htonl(INADDR_ANY);a.sin_port=htons((uint16_t)port);if(bind(fd,(struct sockaddr*)&a,sizeof(a))!=0){close(fd);return -1;}return fd;
}
static inline int64_t kol_udp_send_to(KolStr host,int64_t port,KolStr data) {
    char service[16];snprintf(service,sizeof(service),"%lld",(long long)port);struct addrinfo hints,*res=NULL;memset(&hints,0,sizeof(hints));hints.ai_socktype=SOCK_DGRAM;hints.ai_family=AF_UNSPEC;if(getaddrinfo(kol_str_cstr(&host),service,&hints,&res)!=0)return -1;int64_t sent=-1;
    for(struct addrinfo* it=res;it;it=it->ai_next){int fd=socket(it->ai_family,SOCK_DGRAM,0);if(fd<0)continue;ssize_t n=sendto(fd,kol_str_cstr(&data),kol_str_len(data),0,it->ai_addr,it->ai_addrlen);close(fd);if(n>=0){sent=(int64_t)n;break;}}freeaddrinfo(res);return sent;
}
static inline KolStr kol_udp_recv(int64_t fd,int64_t max_bytes) {if(max_bytes<1)max_bytes=4096;if(max_bytes>1048576)max_bytes=1048576;char* b=(char*)malloc((size_t)max_bytes+1);if(!b)return kol_str_create("");ssize_t n=recvfrom((int)fd,b,(size_t)max_bytes,0,NULL,NULL);if(n<0)n=0;b[n]=0;KolStr r=kol_str_create(b);free(b);return r;}
#else
static inline int64_t kol_net_connect(KolStr h,int64_t p,int64_t t){(void)h;(void)p;(void)t;return -1;}
static inline int64_t kol_net_listen(int64_t p,int64_t b){(void)p;(void)b;return -1;}
static inline int64_t kol_net_accept(int64_t f){(void)f;return -1;}
static inline int64_t kol_net_send(int64_t f,KolStr d){(void)f;(void)d;return -1;}
static inline KolStr kol_net_recv(int64_t f,int64_t n){(void)f;(void)n;return kol_str_create("");}
static inline void kol_net_close(int64_t f){(void)f;}
static inline KolStr kol_net_resolve(KolStr h){(void)h;return kol_str_create("");}
static inline int64_t kol_udp_bind(int64_t p){(void)p;return -1;}
static inline int64_t kol_udp_send_to(KolStr h,int64_t p,KolStr d){(void)h;(void)p;(void)d;return -1;}
static inline KolStr kol_udp_recv(int64_t f,int64_t n){(void)f;(void)n;return kol_str_create("");}
#endif
static inline KolStr kol_http_get_raw(KolStr url, int64_t timeout_ms) {
    const char* u=kol_str_cstr(&url);
    if(strncmp(u,"http://",7)!=0)return kol_str_create("");
    const char* auth=u+7; const char* auth_end=auth+strlen(auth);
    for(const char* q=auth;*q;q++)if(*q=='/'||*q=='?'||*q=='#'){auth_end=q;break;}
    size_t auth_len=(size_t)(auth_end-auth);if(auth_len==0)return kol_str_create("");
    char* authority=(char*)malloc(auth_len+1);if(!authority)return kol_str_create("");memcpy(authority,auth,auth_len);authority[auth_len]=0;
    char* hostname=NULL;int64_t port=80;
    if(authority[0]=='['){char* close=strchr(authority,']');if(!close){free(authority);return kol_str_create("");}size_t hn=(size_t)(close-authority-1);hostname=(char*)malloc(hn+1);if(!hostname){free(authority);return kol_str_create("");}memcpy(hostname,authority+1,hn);hostname[hn]=0;if(close[1]==':')port=atoi(close+2);else if(close[1]!=0){free(hostname);free(authority);return kol_str_create("");}}
    else {char* colon=strrchr(authority,':');if(colon){*colon=0;port=atoi(colon+1);}hostname=(char*)malloc(strlen(authority)+1);if(!hostname){free(authority);return kol_str_create("");}strcpy(hostname,authority);}
    if(!hostname[0]||port<1||port>65535){free(hostname);free(authority);return kol_str_create("");}
    const char* target_src=auth_end;char* target=NULL;
    if(*target_src=='/') {const char* frag=strchr(target_src,'#');size_t n=frag?(size_t)(frag-target_src):strlen(target_src);target=(char*)malloc(n+1);if(target){memcpy(target,target_src,n);target[n]=0;}}
    else if(*target_src=='?') {const char* frag=strchr(target_src,'#');size_t n=frag?(size_t)(frag-target_src):strlen(target_src);target=(char*)malloc(n+2);if(target){target[0]='/';memcpy(target+1,target_src,n);target[n+1]=0;}}
    else {target=(char*)malloc(2);if(target){target[0]='/';target[1]=0;}}
    if(!target){free(hostname);free(authority);return kol_str_create("");}
    KolStr h=kol_str_create(hostname);free(hostname);int64_t fd=kol_net_connect(h,port,timeout_ms);kol_arc_release_str(h);
    if(fd<0){free(target);free(authority);return kol_str_create("");}
    size_t tlen=strlen(target);char* req=(char*)malloc(tlen+auth_len+128);if(!req){kol_net_close(fd);free(target);free(authority);return kol_str_create("");}
    snprintf(req,tlen+auth_len+128,"GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\nUser-Agent: KOL/0.1\r\n\r\n",target,authority);free(target);free(authority);
    KolStr rq=kol_str_create(req);free(req);kol_net_send(fd,rq);kol_arc_release_str(rq);
    size_t cap=8192,len=0;char* out=(char*)malloc(cap);if(!out){kol_net_close(fd);return kol_str_create("");}
    for(;;){if(cap-len<2048){if(cap>=16*1024*1024)break;cap*=2;char* t=(char*)realloc(out,cap);if(!t)break;out=t;}ssize_t n=recv((int)fd,out+len,cap-len-1,0);if(n<=0)break;len+=(size_t)n;if(len>=16*1024*1024)break;}
    out[len]=0;kol_net_close(fd);KolStr result=kol_str_create(out);free(out);return result;
}

static inline KolStr kol_uuid_v4(void) {
    unsigned char b[16]; bool ok=false;
#ifndef _WIN32
    FILE* f=fopen("/dev/urandom","rb");if(f){ok=fread(b,1,sizeof(b),f)==sizeof(b);fclose(f);}
#endif
    if(!ok){uint64_t seed=(uint64_t)time(NULL)^(uint64_t)clock()^(uintptr_t)&b;for(int i=0;i<16;i++){seed^=seed<<13;seed^=seed>>7;seed^=seed<<17;b[i]=(unsigned char)seed;}}
    b[6]=(unsigned char)((b[6]&0x0f)|0x40);b[8]=(unsigned char)((b[8]&0x3f)|0x80);char out[37];snprintf(out,sizeof(out),"%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);return kol_str_create(out);
}

/* Cryptographic digests for text payloads (hex encoded; not password hashing). */
static inline uint32_t kol_rotr32(uint32_t x, uint32_t n) { return (x >> n) | (x << (32-n)); }
static inline KolStr kol_sha256(KolStr input) {
    static const uint32_t k[64]={0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    const unsigned char* src=(const unsigned char*)kol_str_cstr(&input);size_t n=strlen((const char*)src), padded=((n+9+63)/64)*64;unsigned char* msg=(unsigned char*)calloc(padded,1);if(!msg)return kol_str_create("");memcpy(msg,src,n);msg[n]=0x80;uint64_t bits=(uint64_t)n*8;for(int i=0;i<8;i++)msg[padded-1-i]=(unsigned char)(bits>>(8*i));
    uint32_t h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    for(size_t off=0;off<padded;off+=64){uint32_t w[64];for(int i=0;i<16;i++)w[i]=((uint32_t)msg[off+i*4]<<24)|((uint32_t)msg[off+i*4+1]<<16)|((uint32_t)msg[off+i*4+2]<<8)|msg[off+i*4+3];for(int i=16;i<64;i++){uint32_t a=kol_rotr32(w[i-15],7)^kol_rotr32(w[i-15],18)^(w[i-15]>>3),b=kol_rotr32(w[i-2],17)^kol_rotr32(w[i-2],19)^(w[i-2]>>10);w[i]=w[i-16]+a+w[i-7]+b;}uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];for(int i=0;i<64;i++){uint32_t s1=kol_rotr32(e,6)^kol_rotr32(e,11)^kol_rotr32(e,25),ch=(e&f)^(~e&g),t1=z+s1+ch+k[i]+w[i],s0=kol_rotr32(a,2)^kol_rotr32(a,13)^kol_rotr32(a,22),maj=(a&b)^(a&c)^(b&c),t2=s0+maj;z=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;}h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;}
    free(msg);char out[65];for(int i=0;i<8;i++)snprintf(out+i*8,9,"%08x",h[i]);out[64]=0;return kol_str_create(out);
}
static inline uint32_t kol_rotl32(uint32_t x,uint32_t n){return (x<<n)|(x>>(32-n));}
static inline KolStr kol_md5(KolStr input) {
    static const uint32_t k[64]={0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,0xa8304613,0xfd469501,0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,0x6b901122,0xfd987193,0xa679438e,0x49b40821,0xf61e2562,0xc040b340,0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,0x676f02d9,0x8d2a4c8a,0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,0x289b7ec6,0xeaa127fa,0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,0xffeff47d,0x85845dd1,0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391};
    static const uint32_t r[64]={7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21};
    const unsigned char* src=(const unsigned char*)kol_str_cstr(&input);size_t n=strlen((const char*)src),padded=((n+9+63)/64)*64;unsigned char* msg=(unsigned char*)calloc(padded,1);if(!msg)return kol_str_create("");memcpy(msg,src,n);msg[n]=0x80;uint64_t bits=(uint64_t)n*8;for(int i=0;i<8;i++)msg[padded-8+i]=(unsigned char)(bits>>(8*i));uint32_t h0=0x67452301,h1=0xefcdab89,h2=0x98badcfe,h3=0x10325476;
    for(size_t off=0;off<padded;off+=64){uint32_t m[16];for(int i=0;i<16;i++)m[i]=(uint32_t)msg[off+i*4]|((uint32_t)msg[off+i*4+1]<<8)|((uint32_t)msg[off+i*4+2]<<16)|((uint32_t)msg[off+i*4+3]<<24);uint32_t a=h0,b=h1,c=h2,d=h3;for(uint32_t i=0;i<64;i++){uint32_t f,g;if(i<16){f=(b&c)|(~b&d);g=i;}else if(i<32){f=(d&b)|(~d&c);g=(5*i+1)%16;}else if(i<48){f=b^c^d;g=(3*i+5)%16;}else{f=c^(b|~d);g=(7*i)%16;}uint32_t temp=d;d=c;c=b;b=b+kol_rotl32(a+f+k[i]+m[g],r[i]);a=temp;}h0+=a;h1+=b;h2+=c;h3+=d;}
    free(msg);char out[33];snprintf(out,9,"%08x",((h0&0x000000ff)<<24)|((h0&0x0000ff00)<<8)|((h0&0x00ff0000)>>8)|((h0&0xff000000)>>24));snprintf(out+8,9,"%08x",((h1&0x000000ff)<<24)|((h1&0x0000ff00)<<8)|((h1&0x00ff0000)>>8)|((h1&0xff000000)>>24));snprintf(out+16,9,"%08x",((h2&0x000000ff)<<24)|((h2&0x0000ff00)<<8)|((h2&0x00ff0000)>>8)|((h2&0xff000000)>>24));snprintf(out+24,9,"%08x",((h3&0x000000ff)<<24)|((h3&0x0000ff00)<<8)|((h3&0xff000000)>>8)|((h3&0xff000000)>>24));out[32]=0;return kol_str_create(out);
}
#endif /* KOL_RUNTIME_H */
