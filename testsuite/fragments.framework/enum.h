
#include <Cocoa/Cocoa.h>

typedef NS_ENUM(NSInteger, BasicEnum) {
    BasicEnumValue1,
    BasicEnumValue2,
    BasicEnumValue3
};

typedef NS_ENUM(NSUInteger, ValuedEnum) {
    ValuedEnumValue1 = 1,
    ValuedEnumValue2 = 2,
    ValuedEnumValue3 = 3
};

typedef NS_ENUM(NSInteger, DeprecatedEnumValue) {
    DeprecatedEnumValue1 = 1,
    DeprecatedEnumValue2 API_DEPRECATED("", macos(10.6, 10.9)) = 2,
};

typedef NS_ENUM(NSInteger, DeprecatedEnum) {
    DeprecatedEnum1,
} API_DEPRECATED("", macos(10.0, 10.11));

typedef NS_OPTIONS(NSUInteger, BasicOptions) {
    Option1 = 1 << 1,
    Option2 = 1 << 2,
    Option3 = 1 << 3,
    Option8 = 1 << 8,
};

typedef enum {
    v1 = 1,
    v2 = 2
} CUnnamedEnum;

typedef enum C_Named_Enum {
    nv1 = 1,
    nv2 = 2
} CNamedEnum;


enum {
    value_in_unnamed_enum1 = 1,
    value_in_unnamed_enum2 = 2,
};
