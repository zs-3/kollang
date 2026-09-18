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
    char data[];
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
        KolStrHeap* h = (KolStrHeap*)malloc(sizeof(KolStrHeap) + len + 1);
        if (!h) {
            fprintf(stderr, "Out of memory in kol_str_create\n");
            exit(1);
        }
        h->ref_count = 1;
        h->len = len;
        memcpy(h->data, cstr, len);
        h->data[len] = '\0';
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

static inline void kol_arc_retain_str(KolStr s) {
    if (s.is_heap && s.heap.ptr) {
        s.heap.ptr->ref_count++;
    }
}

static inline void kol_arc_release_str(KolStr s) {
    if (s.is_heap && s.heap.ptr) {
        s.heap.ptr->ref_count--;
        if (s.heap.ptr->ref_count == 0) {
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
    const char* ca = kol_str_cstr(&a);
    const char* cb = kol_str_cstr(&b);
    size_t la = kol_str_len(a);
    size_t lb = kol_str_len(b);
    size_t total = la + lb;
    char* buf = (char*)malloc(total + 1);
    memcpy(buf, ca, la);
    memcpy(buf + la, cb, lb);
    buf[total] = '\0';
    KolStr res = kol_str_create(buf);
    free(buf);
    return res;
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
