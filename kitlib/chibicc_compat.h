/* chibicc compatibility shim for the Kol runtime.
 *
 * Kit passes this with `-include` ONLY when compiling with chibicc; the Kol
 * runtime itself is not modified and gcc/clang builds never see this file.
 *
 * chibicc has two genuinely atomic primitives - __builtin_compare_and_swap
 * (lock cmpxchg) and __builtin_atomic_exchange (xchg) - but its <stdatomic.h>
 * fetch_add/load/store macros are plain, NON-atomic C operations, so they must
 * not be used for the runtime's shared counters.  The GCC __atomic_* builtins
 * the runtime calls are therefore built here from CAS and xchg.
 *
 * x86-64 only (as is chibicc): aligned loads are atomic and have acquire
 * semantics, stores through xchg are sequentially consistent.
 */
#ifndef KIT_CHIBICC_COMPAT_H
#define KIT_CHIBICC_COMPAT_H

#define __ATOMIC_RELAXED 0
#define __ATOMIC_CONSUME 1
#define __ATOMIC_ACQUIRE 2
#define __ATOMIC_RELEASE 3
#define __ATOMIC_ACQ_REL 4
#define __ATOMIC_SEQ_CST 5

#define __atomic_load_n(p, order) (*(p))

#define __atomic_store_n(p, v, order) ((void)__builtin_atomic_exchange((p), (v)))

#define __atomic_exchange_n(p, v, order) __builtin_atomic_exchange((p), (v))

#define __atomic_compare_exchange_n(p, expected, desired, weak, success, failure) \
  __builtin_compare_and_swap((p), (expected), (desired))

#define __atomic_add_fetch(p, v, order) ({ \
  typeof(*(p)) *kit_p_ = (p); \
  typeof(*(p)) kit_old_ = *kit_p_; \
  typeof(*(p)) kit_new_; \
  do { kit_new_ = kit_old_ + (v); } \
  while (!__builtin_compare_and_swap(kit_p_, &kit_old_, kit_new_)); \
  kit_new_; })

#define __atomic_sub_fetch(p, v, order) ({ \
  typeof(*(p)) *kit_p_ = (p); \
  typeof(*(p)) kit_old_ = *kit_p_; \
  typeof(*(p)) kit_new_; \
  do { kit_new_ = kit_old_ - (v); } \
  while (!__builtin_compare_and_swap(kit_p_, &kit_old_, kit_new_)); \
  kit_new_; })

#define __builtin_ia32_pause() ((void)0)

/* glibc's <regex.h> uses a prototype syntax chibicc cannot parse, and the Kol
 * runtime includes it for std.regex.  Pre-empt it with the few declarations the
 * runtime uses.  Layout matches glibc on x86-64 (sizeof(regex_t) == 64,
 * regoff_t == int), which is the only platform chibicc supports. */
#if defined(__linux__) && defined(__x86_64__)
#define _REGEX_H 1
typedef struct { char kit_opaque_[64]; } regex_t;
typedef int regoff_t;
typedef struct { regoff_t rm_so; regoff_t rm_eo; } regmatch_t;
#define REG_EXTENDED 1
#define REG_ICASE 2
#define REG_NEWLINE 4
#define REG_NOSUB 8
#define REG_NOTBOL 1
#define REG_NOMATCH 1
int regcomp(regex_t *preg, const char *pattern, int cflags);
int regexec(const regex_t *preg, const char *string, unsigned long nmatch, regmatch_t *pmatch, int eflags);
void regfree(regex_t *preg);
#endif

#endif
