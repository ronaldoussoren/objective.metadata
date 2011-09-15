PyObjC metadata generator using objective.cparser


This project is hopelessly incomplete at the moment

TODO:
- Enhance the parser module to collect all useful definitions
- Add useful storage format (probably pretty-printed JSON)
- Merge information from several parses:
  - i386, x86_64, ppc
  - OSX 10.5, 10.6, 10.7

- Extract those bits that are genuinely useful:

  * Constant definitions
  * Variable defintions (extern NSString* NSFoo ...)
  * Function definitions
  * Methods with "interesting" prototypes
    (ptr-to-value arguments, special types like BOOL)
  * Informal protocols

- Add automatic enrichment of some "intersting" prototypes
  * an 'NSError**' argument is almost certainly a
    by reference output argument, null allowed,
  * CFCreateFoo() that returns a CFType returns a value
    that is CFRetained, 
  ...

- Add mechanisme for adding metadata
  * Specify argument annotations
  * Possibly add new definitions
  * Specify that some definitions should not be wrapped

