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

static inline bool kol_str_eq(KolStr a, KolStr b) {
    size_t la = kol_str_len(a);
    size_t lb = kol_str_len(b);
    if (la != lb) return false;
    return strcmp(kol_str_cstr(&a), kol_str_cstr(&b)) == 0;
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

static inline kol_array_t kol_array_create(size_t elem_size, size_t initial_capacity);
static inline void kol_array_push(kol_array_t* arr, const void* elem);

static inline kol_array_t kol_str_split(KolStr s, KolStr sep) {
    kol_array_t arr = kol_array_create(sizeof(KolStr), 4);
    const char* cs = kol_str_cstr(&s);
    const char* csep = kol_str_cstr(&sep);
    size_t seplen = kol_str_len(sep);
    if (seplen == 0) {
        KolStr item = kol_str_create(cs);
        kol_array_push(&arr, &item);
        return arr;
    }
    const char* start = cs;
    const char* found;
    while ((found = strstr(start, csep)) != NULL) {
        size_t part_len = found - start;
        char* buf = (char*)malloc(part_len + 1);
        memcpy(buf, start, part_len);
        buf[part_len] = '\0';
        KolStr item = kol_str_create(buf);
        free(buf);
        kol_array_push(&arr, &item);
        start = found + seplen;
    }
    KolStr item = kol_str_create(start);
    kol_array_push(&arr, &item);
    return arr;
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

static inline void kol_array_pop(kol_array_t* a) {
    if (a->len > 0) {
        a->len--;
    }
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

#endif /* KOL_RUNTIME_H */
