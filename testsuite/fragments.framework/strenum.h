#include <Cocoa/Cocoa.h>

typedef NSString * SomeStringEnum NS_STRING_ENUM;

extern SomeStringEnum const SomeStringValue1;
extern SomeStringEnum const SomeStringValue2 API_AVAILABLE(macos(10.7));
extern SomeStringEnum const SomeStringValue3 API_DEPRECATED("message", macos(10.7, 10.8));
