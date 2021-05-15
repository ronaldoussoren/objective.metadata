#import <Cocoa/Cocoa.h>

static int StaticIntx = 5;
static int StaticInt = 5;
static NSInteger StaticNSInt = 42;
static float StaticFloat = 2.5;

static NSUInteger MyAlias = NSWindowSharingNone;

static  NSInteger deprecated API_DEPRECATED("message", macos(10.5, 10.9)) = 55;
static  NSInteger available API_AVAILABLE(macos(10.13)) = 99;

static  NSInteger deprecated_alias API_DEPRECATED("message", macos(10.5, 10.9)) = NSWindowSharingReadWrite;
