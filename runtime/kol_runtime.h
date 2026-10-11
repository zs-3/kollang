#ifndef KOL_RUNTIME_H
#define KOL_RUNTIME_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <ctype.h>
#include <math.h>
#include <pthread.h>

/* ============================================================================
 * PANIC AND ERRORS
 * ============================================================================ */

static inline void kol_panic(const char* file, int line, const char* message) {
    fprintf(stderr, "Kol Panic at %s:%d: %s\n", file, line, message);
    exit(1);
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
        s.heap.ptr->ref_count++;
    }
}

static inline void kol_arc_release_str(KolStr s) {
    if (s.is_heap && s.heap.ptr) {
        s.heap.ptr->ref_count--;
        if (s.heap.ptr->ref_count == 0) {
            if (s.heap.ptr->data) free(s.heap.ptr->data);
            free(s.heap.ptr);
        }
    }
}

/* release `s` unless the concat result `res` reuses its buffer */
static inline void kol_arc_release_str_unless(KolStr s, KolStr res) {
    if (s.is_heap && res.is_heap && s.heap.ptr == res.heap.ptr) return;
    kol_arc_release_str(s);
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

    if (a.is_heap && a.heap.ptr && a.heap.ptr->ref_count == 1 && total <= a.heap.ptr->capacity) {
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

/* independent copy of an array (value semantics for `let b = a`, `b = a`) */
static inline kol_array_t kol_array_clone(const kol_array_t* a) {
    kol_array_t c = kol_array_create(a->elem_size, a->len);
    if (a->len > 0 && a->data) {
        memcpy(c.data, a->data, a->len * a->elem_size);
    }
    c.len = a->len;
    return c;
}

/* clone of a [str]: every element gains a reference */
static inline kol_array_t kol_array_clone_strs(const kol_array_t* a) {
    kol_array_t c = kol_array_clone(a);
    KolStr* strs = (KolStr*)c.data;
    for (size_t i = 0; i < c.len; i++) kol_arc_retain_str(strs[i]);
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
 * CHANNELS
 * ============================================================================ */

typedef struct {
    void* data;
    size_t elem_size;
    int has_value;
    pthread_mutex_t mutex;
    pthread_cond_t  can_send;
    pthread_cond_t  can_recv;
    int closed;
} KolChannel;

static inline KolChannel* kol_channel_create(size_t elem_size) {
    KolChannel* ch = (KolChannel*)malloc(sizeof(KolChannel));
    ch->data = malloc(elem_size);
    ch->elem_size = elem_size;
    ch->has_value = 0;
    ch->closed = 0;
    pthread_mutex_init(&ch->mutex, NULL);
    pthread_cond_init(&ch->can_send, NULL);
    pthread_cond_init(&ch->can_recv, NULL);
    return ch;
}

static inline void kol_channel_send(KolChannel* ch, void* value) {
    pthread_mutex_lock(&ch->mutex);
    while (ch->has_value && !ch->closed)
        pthread_cond_wait(&ch->can_send, &ch->mutex);
    if (!ch->closed) {
        memcpy(ch->data, value, ch->elem_size);
        ch->has_value = 1;
        pthread_cond_signal(&ch->can_recv);
    }
    pthread_mutex_unlock(&ch->mutex);
}

static inline void kol_channel_recv(KolChannel* ch, void* out) {
    pthread_mutex_lock(&ch->mutex);
    while (!ch->has_value && !ch->closed)
        pthread_cond_wait(&ch->can_recv, &ch->mutex);
    if (ch->has_value) {
        memcpy(out, ch->data, ch->elem_size);
        ch->has_value = 0;
        pthread_cond_signal(&ch->can_send);
    }
    pthread_mutex_unlock(&ch->mutex);
}

static inline void kol_channel_close(KolChannel* ch) {
    pthread_mutex_lock(&ch->mutex);
    ch->closed = 1;
    pthread_cond_broadcast(&ch->can_recv);
    pthread_cond_broadcast(&ch->can_send);
    pthread_mutex_unlock(&ch->mutex);
}

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

static inline void kol_sleep_ms(int64_t ms) {
    if (ms <= 0) return;
    struct timespec ts;
    ts.tv_sec = (time_t)(ms / 1000);
    ts.tv_nsec = (long)((ms % 1000) * 1000000L);
    nanosleep(&ts, NULL);
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

#endif /* KOL_RUNTIME_H */
