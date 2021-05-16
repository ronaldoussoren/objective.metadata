#import <Cocoa/Cocoa.h>

extern double __private_function(int);

extern int function1(void);
extern float function2(int a);
extern void function3(int, float);


extern int vararg_function(int, ...);

/* K&R  didn't have prototypes.
 * Sometimes seen in headers, mostly when
 * someone confuses C++ and C argumentless function prototypes.
 */
extern int kandr_function();

static inline int static_inline_function(int);
extern inline double extern_inline_function(float);
