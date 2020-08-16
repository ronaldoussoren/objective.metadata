"""
Utility module for parsing the header files in a framework and extracting
interesting definitions using Clang.

I realize that my "categories" on the libclang python classes are more Obj-C-ish
than "pythonic" but, hey, I'm an ObjC developer first...
"""

import collections
import os
from ctypes import POINTER, byref, c_int, c_uint
from itertools import chain

import clang
import objc
from clang.cindex import (
    BaseEnumeration,
    Cursor,
    CursorKind,
    SourceRange,
    Structure,
    TranslationUnit,
    Type,
    TypeKind,
    _CXString,
)

# Add missing bindings...
CursorKind.OBJCRETURNSINNERPOINTER_ATTR = CursorKind(429)
CursorKind.REQUIRESSUPER_ATTR = CursorKind(430)
CursorKind.EXPLICITPROTOCOLIMPL_ATTR = CursorKind(433)
CursorKind.OBJCDESIGNATEDINITIALIZER_ATTR = CursorKind(434)
CursorKind.OBJCBOXABLE_ATTR = CursorKind(436)
CursorKind.FLAGENUM_ATTR = CursorKind(437)

TranslationUnit.INCLUDE_ATTRIBUTED_TYPES = 0x1000
TranslationUnit.VISIT_IMPLICIT_ATTRIBUTES = 0x2000

TypeKind.ATTRIBUTED = TypeKind(163)


# Add ctypes wrappers for a bunch of functions not
# yet added to the real libclang python binding.
clang.cindex.register_function(
    clang.cindex.conf.lib, ("clang_getCursorLinkage", [Cursor], c_int), False
)
clang.cindex.register_function(
    clang.cindex.conf.lib, ("clang_Cursor_isObjCOptional", [Cursor], c_uint), False
)
clang.cindex.register_function(
    clang.cindex.conf.lib, ("clang_getCXXAccessSpecifier", [Cursor], c_int), False
)
clang.cindex.register_function(
    clang.cindex.conf.lib, ("clang_Cursor_isVariadic", [Cursor], c_uint), False
)
clang.cindex.register_function(
    clang.cindex.conf.lib,
    ("clang_Cursor_getObjCDeclQualifiers", [Cursor], c_uint),
    False,
)
clang.cindex.register_function(
    clang.cindex.conf.lib,
    ("clang_Cursor_getObjCPropertyAttributes", [Cursor, c_uint], c_uint),
    False,
)
clang.cindex.register_function(
    clang.cindex.conf.lib,
    ("clang_getCursorResultType", [Cursor], Type, Type.from_result),
    False,
)
clang.cindex.register_function(
    clang.cindex.conf.lib, ("clang_Type_getNullability", [Type], c_uint), False
)
clang.cindex.register_function(
    clang.cindex.conf.lib, ("clang_Type_getModifiedType", [Type], Type), False
)

# objc module was missing this:
objc._C_BYREF = "R"

###
# A Class to hold qualifiers
###


class ObjcDeclQualifier(object):
    Flag_None = 0x0
    Flag_In = 0x1
    Flag_InOut = 0x2
    Flag_Out = 0x4
    Flag_Bycopy = 0x8
    Flag_Byref = 0x10
    Flag_Oneway = 0x20
    # noinspection PyProtectedMember
    __val_to_encode_string = {
        Flag_In: objc._C_IN,
        Flag_InOut: objc._C_INOUT,
        Flag_Out: objc._C_OUT,
        Flag_Bycopy: objc._C_BYCOPY,
        Flag_Byref: objc._C_BYREF,
        Flag_Oneway: objc._C_ONEWAY,
    }

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return self.value == other.value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        string = "ObjcDeclQualifier("
        found_any = False
        for attr in dir(self):
            if attr.startswith("Flag_"):
                flag_val = getattr(self, attr)
                if bool(self.value & flag_val):
                    if found_any:
                        string += "|"
                    string += attr[5:]
                    found_any = True
        if not found_any:
            string += "None"
        string += ")"
        return string

    @staticmethod
    def from_encode_string(string):
        val = ObjcDeclQualifier.Flag_None
        string = string or ""
        for value, encode_string in ObjcDeclQualifier.__val_to_encode_string.items():
            if encode_string in string:
                val = val | value
        return ObjcDeclQualifier(val)

    def to_encode_string(self):
        string = ""
        for value, encode_string in ObjcDeclQualifier.__val_to_encode_string.items():
            if bool(self.value & value):
                string = string + encode_string
        return string

    def add_flags(self, other):
        if not isinstance(other, ObjcDeclQualifier):
            other = ObjcDeclQualifier(int(other))
        self.value = self.value | other.value

    def subtract_flags(self, other):
        if not isinstance(other, ObjcDeclQualifier):
            other = ObjcDeclQualifier(int(other))
        self.value = self.value & (~other.value)

    @property
    def is_in(self):
        return bool(self.value & self.Flag_In)

    @is_in.setter
    def is_in(self, value):
        if value:
            self.value |= ObjcDeclQualifier.Flag_In
        else:
            self.value &= ~ObjcDeclQualifier.Flag_In

    @property
    def is_inout(self):
        return bool(self.value & self.Flag_InOut)

    @is_inout.setter
    def is_inout(self, value):
        if value:
            self.value |= ObjcDeclQualifier.Flag_InOut
        else:
            self.value &= ~ObjcDeclQualifier.Flag_InOut

    @property
    def is_out(self):
        return bool(self.value & self.Flag_Out)

    @is_out.setter
    def is_out(self, value):
        if value:
            self.value |= ObjcDeclQualifier.Flag_Out
        else:
            self.value &= ~ObjcDeclQualifier.Flag_Out

    @property
    def is_bycopy(self):
        return bool(self.value & self.Flag_Bycopy)

    @is_bycopy.setter
    def is_bycopy(self, value):
        if value:
            self.value |= ObjcDeclQualifier.Flag_Bycopy
        else:
            self.value &= ~ObjcDeclQualifier.Flag_Bycopy

    @property
    def is_byref(self):
        return bool(self.value & self.Flag_Byref)

    @is_byref.setter
    def is_byref(self, value):
        if value:
            self.value |= ObjcDeclQualifier.Flag_Byref
        else:
            self.value &= ~ObjcDeclQualifier.Flag_Byref

    @property
    def is_oneway(self):
        return bool(self.value & self.Flag_Oneway)

    @is_oneway.setter
    def is_oneway(self, value):
        if value:
            self.value |= ObjcDeclQualifier.Flag_Oneway
        else:
            self.value &= ~ObjcDeclQualifier.Flag_Oneway

    @property
    def is_none(self):
        return self.value == self.Flag_None

    @is_none.setter
    def is_none(self, value):
        if value:
            self.value = self.Flag_None
        else:
            raise ValueError(value)


class LinkageKind(object):
    """
    A class to hold linkage specifiers.
    """

    INVALID = 0
    NOLINKAGE = 1
    INTERNAL = 2
    UNIQUEEXTERNAL = 3
    EXTERNAL = 4

    # The unique kind objects, indexed by id.
    _kinds = []
    _name_map = None

    def __init__(self, value):
        if value >= len(LinkageKind._kinds):
            LinkageKind._kinds += [None] * (value - len(LinkageKind._kinds) + 1)
        if LinkageKind._kinds[value] is not None:
            raise ValueError("LinkageKind already loaded")
        self.value = value
        self._name_map = {}
        LinkageKind._kinds[value] = self
        LinkageKind._name_map = None

    def from_param(self):
        return self.value

    @property
    def name(self):
        """Get the enumeration name of this cursor kind."""
        if self._name_map == {}:
            for key, value in LinkageKind.__dict__.items():
                if isinstance(value, LinkageKind):
                    self._name_map[value] = key

        return self._name_map[self]

    @staticmethod
    def from_id(linkage_id):
        if (
            linkage_id >= len(LinkageKind._kinds)
            or LinkageKind._kinds[linkage_id] is None
        ):
            raise ValueError("Unknown type kind %d" % (linkage_id,))
        return LinkageKind._kinds[linkage_id]

    def __repr__(self):
        return "LinkageKind.%s" % (self.name,)


# I was stupidly following the libclang enum pattern when I made this...
LinkageKind.INVALID = LinkageKind(0)
LinkageKind.NOLINKAGE = LinkageKind(1)
LinkageKind.INTERNAL = LinkageKind(2)
LinkageKind.UNIQUEEXTERNAL = LinkageKind(3)
LinkageKind.EXTERNAL = LinkageKind(4)


class AbstractClangVisitor(object):
    ###
    # A Visitor class to traverse libclang cursors
    ###

    def visitor_function_for_cursor(self, cursor):
        method_name = "visit_" + cursor.kind.name.lower()
        method = getattr(self, method_name, None)
        if method is None:
            method = self.descend
        return method

    def visit(self, cursor):
        visitor_function = self.visitor_function_for_cursor(cursor)
        return None if visitor_function is None else visitor_function(cursor)

    def descend(self, cursor):
        for c in cursor.get_children():
            self.visit(c)


###
# Additions to clang.cindex.Type
###


def _type_decl_getter(self):
    """

    :param self:
    :return:
    """
    decl = self.get_declaration()
    if decl.kind == CursorKind.NO_DECL_FOUND:
        return None
    else:
        return decl


Type.declaration = property(fget=_type_decl_getter)


def _type_get_quals(self):
    quals = []
    if self.is_const_qualified():
        quals.append("const")
    if self.is_volatile_qualified():
        quals.append("volatile")
    if self.is_restrict_qualified():
        quals.append("restrict")

    return quals if len(quals) > 0 else None


Type.quals = property(fget=_type_get_quals)

# The helpful folks who produced the clang python bindings got a few things wrong.
# For instance, they assert that a type is a FUNCTIONPROTO before allowing you
# to enumerate arguments
# or ask if it's variadic. That's nice, but it doesn't cover block parameter
# declarations, which can also answer those questions


def _type_argument_types(self):
    """Retrieve a container for the non-variadic arguments for this type.

    The returned object is iterable and indexable. Each item in the
    container is a Type instance.
    """

    class ArgumentsIterator(collections.Sequence):
        def __init__(self, parent):
            self.parent = parent
            self.length = None

        def __len__(self):
            if self.length is None:
                self.length = clang.cindex.conf.lib.clang_getNumArgTypes(self.parent)

            return self.length

        def __getitem__(self, key):
            # libclang had a fix-me here of: Support slice objects.
            if not isinstance(key, int):
                raise TypeError("Must supply a non-negative int.")

            if key < 0:
                raise IndexError("Only non-negative indexes are accepted.")

            if key >= len(self):
                raise IndexError(
                    "Index greater than container length: " "%d > %d" % (key, len(self))
                )

            result = clang.cindex.conf.lib.clang_getArgType(self.parent, key)
            if result.kind == TypeKind.INVALID:
                raise IndexError("Argument could not be retrieved.")

            return result

    return ArgumentsIterator(self)


Type.argument_types = _type_argument_types


def _type_is_function_variadic(self):
    """Determine whether this function Type is a variadic function type."""
    # assert self.kind == TypeKind.FUNCTIONPROTO
    return clang.cindex.conf.lib.clang_isFunctionTypeVariadic(self)


Type.is_function_variadic = _type_is_function_variadic


def _type_looks_like_function(self):
    if self.kind == TypeKind.FUNCTIONPROTO or self.kind == TypeKind.FUNCTIONNOPROTO:
        return True

    if self.kind == TypeKind.BLOCKPOINTER:
        return False  # We're not a function, we're a block.

    return_type = self.get_result()
    if return_type.kind != TypeKind.INVALID:
        return True

    if self.kind == TypeKind.POINTER:
        pointee = self.get_pointee()
        return pointee.looks_like_function

    canonical = self.get_canonical()
    if canonical != self:
        return canonical.looks_like_function

    return False


Type.looks_like_function = property(fget=_type_looks_like_function)


def _type_pointee(self):
    pointee = self.get_pointee()
    if pointee and pointee.kind != TypeKind.INVALID and pointee != self:
        return pointee
    return None


Type.pointee = property(fget=_type_pointee)

# Imagine that! The libclang implementation of Cursor.result_type is
# just dead wrong (goes through the type)
# Move that property over from Cursor to Type, then replace the
# Cursor implementation later
Type.result_type = Cursor.result_type

Type.valid_type = property(
    fget=lambda self: None if self.kind == TypeKind.INVALID else self
)


def _type_ever_passes_test(self, pred):
    if pred(self):
        return True
    elif self.kind == TypeKind.TYPEDEF:
        td = self.declaration
        ult = None if td is None else td.underlying_typedef_type_valid
        if ult:
            return ult.ever_passes_test(pred)

    return False


Type.ever_passes_test = _type_ever_passes_test


def _type_ever_defines_to(self, type_name):
    return self.ever_passes_test(
        lambda x: (
            getattr(x, "spelling", "") == type_name
            or (x.declaration and x.declaration.spelling == type_name)
        )
    )


Type.ever_defines_to = _type_ever_defines_to


def _type_next_typedefed_type(self):
    if self.kind != TypeKind.TYPEDEF:
        return None
    td = self.declaration
    ult = None if td is None else td.underlying_typedef_type_valid
    return ult


Type.next_typedefed_type = property(fget=_type_next_typedefed_type)


def _type_element_count(self):
    """Retrieve the number of elements in this type.

    Replacing the method from the libclang python bindings because
    the stock one throws an exception,
    of type *Exception*. Sheesh.

    Returns an int.

    If the Type is not an array or vector, this returns None
    """
    result = clang.cindex.conf.lib.clang_getNumElements(self)
    if result < 0:
        return None

    return result


Type.element_count = property(fget=_type_element_count)

###
# Additions to clang.cindex.TypeKind
###

# noinspection PyProtectedMember
__typekind_to_objc_types_map_common = {
    # The comments are the list of Clang's "built-ins"
    # Not sure what to do about some of the more esoteric ones
    # CXType_Void = 2,
    TypeKind.VOID: objc._C_VOID,
    # CXType_Bool = 3,
    TypeKind.BOOL: objc._C_BOOL,
    # CXType_Char_U = 4,
    TypeKind.CHAR_U: objc._C_UCHR,
    # CXType_UChar = 5,
    TypeKind.UCHAR: objc._C_UCHR,
    # CXType_Char16 = 6,
    TypeKind.CHAR16: objc._C_USHT,  # determined empirically
    # CXType_Char32 = 7,
    TypeKind.CHAR32: objc._C_UINT,  # determined empirically
    # CXType_UShort = 8,
    TypeKind.USHORT: objc._C_USHT,
    # CXType_UInt = 9,
    TypeKind.UINT: objc._C_UINT,
    # CXType_ULongLong = 11,
    TypeKind.ULONGLONG: objc._C_ULNGLNG,
    # CXType_UInt128 = 12,
    TypeKind.UINT128: "T",  # determined empirically
    # CXType_Char_S = 13,
    TypeKind.CHAR_S: objc._C_CHR,
    # CXType_SChar = 14,
    TypeKind.SCHAR: objc._C_CHR,
    # CXType_WChar = 15,
    TypeKind.WCHAR: objc._C_INT,  # determined empirically
    # CXType_Short = 16,
    TypeKind.SHORT: objc._C_SHT,
    # CXType_Int = 17,
    TypeKind.INT: objc._C_INT,
    # CXType_LongLong = 19,
    TypeKind.LONGLONG: objc._C_LNGLNG,
    # CXType_Int128 = 20,
    TypeKind.INT128: "t",  # determined empirically
    # CXType_Float = 21,
    TypeKind.FLOAT: objc._C_FLT,
    # CXType_Double = 22,
    TypeKind.DOUBLE: objc._C_DBL,
    # CXType_LongDouble = 23,
    TypeKind.LONGDOUBLE: "D",  # determined empirically
    # CXType_NullPtr = 24,
    # CXType_Overload = 25,
    # CXType_Dependent = 26,
    # CXType_ObjCId = 27,
    TypeKind.OBJCID: objc._C_ID,
    # CXType_ObjCClass = 28,
    TypeKind.OBJCCLASS: objc._C_CLASS,
    # CXType_ObjCSel = 29,
    TypeKind.OBJCSEL: objc._C_SEL,
    TypeKind.BLOCKPOINTER: objc._C_ID + objc._C_UNDEF,
}

# noinspection PyProtectedMember
__typekind_to_objc_types_map_64 = {
    # CXType_ULong = 10,
    TypeKind.ULONG: objc._C_ULNG_LNG,
    # CXType_Long = 18,
    TypeKind.LONG: objc._C_LNG_LNG,
}

# noinspection PyProtectedMember
__typekind_to_objc_types_map_32 = {
    # CXType_ULong = 10,
    TypeKind.ULONG: objc._C_ULNG,
    # CXType_Long = 18,
    TypeKind.LONG: objc._C_LNG,
}

__typekind_by_arch_map = {
    "arm64": dict(
        chain(
            __typekind_to_objc_types_map_common.items(),
            __typekind_to_objc_types_map_64.items(),
        )
    ),
    "x86_64": dict(
        chain(
            __typekind_to_objc_types_map_common.items(),
            __typekind_to_objc_types_map_64.items(),
        )
    ),
    "ppc64": dict(
        chain(
            __typekind_to_objc_types_map_common.items(),
            __typekind_to_objc_types_map_64.items(),
        )
    ),
    "i386": dict(
        chain(
            __typekind_to_objc_types_map_common.items(),
            __typekind_to_objc_types_map_32.items(),
        )
    ),
}


def _typekind_objc_type_for_arch(self, arch):
    assert arch is not None
    typekind_by_arch_map = __typekind_by_arch_map.get(arch, None)
    assert typekind_by_arch_map is not None
    typekind = typekind_by_arch_map.get(self)
    return typekind


TypeKind.objc_type_for_arch = _typekind_objc_type_for_arch

###
# Additions to clang.cindex.Cursor
###


def _cursor_get_category_class_cursor(self):
    if self.kind != CursorKind.OBJC_CATEGORY_DECL:
        return None

    first_child_cursor = None
    for child in self.get_children():
        first_child_cursor = child
        break

    if (
        first_child_cursor is None
        or first_child_cursor.kind != CursorKind.OBJC_CLASS_REF
    ):
        return None

    return first_child_cursor


Cursor.get_category_class_cursor = _cursor_get_category_class_cursor


def _cursor_get_category_class_name(self):
    category_class_cursor = self.get_category_class_cursor()
    return (
        None
        if category_class_cursor is None
        else getattr(category_class_cursor.type, "spelling", None)
    )


Cursor.get_category_class_name = _cursor_get_category_class_name

Cursor.get_category_name = (
    lambda self: None if self.kind != CursorKind.OBJC_CATEGORY_DECL else self.spelling
)

Cursor.get_is_informal_protocol = (
    lambda self: False
    if self.kind != CursorKind.OBJC_CATEGORY_DECL
    else (self.get_category_class_name() == "NSObject" or "Delegate" in self.spelling)
)


def _cursor_get_adopted_protocol_nodes(self):
    if self.kind not in [
        CursorKind.OBJC_CATEGORY_DECL,
        CursorKind.OBJC_INTERFACE_DECL,
        CursorKind.OBJC_PROTOCOL_DECL,
    ]:
        return None

    protocols = []
    for child in self.get_children():
        if child.kind == CursorKind.OBJC_PROTOCOL_REF:
            protocols.append(child)

    return protocols


Cursor.get_adopted_protocol_nodes = _cursor_get_adopted_protocol_nodes

Cursor.token_string = property(
    fget=lambda self: "".join([token.spelling for token in self.get_tokens()])
)


def _sourcerange_get_raw_contents(self):
    contents = None

    start_file_name = self.start.file.name
    end_file_name = self.end.file.name
    if start_file_name == end_file_name and os.path.exists(start_file_name):
        with open(start_file_name) as fp:
            fp.seek(self.start.offset)
            contents = fp.read(self.end.offset - self.start.offset)

    return contents


SourceRange.get_raw_contents = _sourcerange_get_raw_contents


def _cursor_get_struct_field_decls(self):
    if self.kind != CursorKind.STRUCT_DECL:
        return None
    return filter(lambda x: x.kind == CursorKind.FIELD_DECL, self.get_children())


Cursor.get_struct_field_decls = _cursor_get_struct_field_decls


def _cursor_get_function_specifiers(self):
    if self.kind != CursorKind.FUNCTION_DECL:
        return None

    # function specifiers are "inline", "explicit" and
    # "virtual" the last two being C++ only
    func_name = self.spelling
    func_specs = set()

    if self.is_virtual:
        func_specs.add("virtual")

    # I was originally doing this by iterating tokens, but
    # that expanded macros, which expanded #if/#else macros
    # some clauses of which had inline in them and was causing false alarms.
    # Hopefully this is better.

    raw_string = self.extent.get_raw_contents()
    before_func = raw_string.split(func_name)[0]

    if "inline" in before_func or "INLINE" in before_func:
        func_specs.add("inline")
    if "virtual" in before_func:
        func_specs.add("virtual")
    if "explicit" in before_func:
        func_specs.add("explicit")

    return list(func_specs)


Cursor.get_function_specifiers = _cursor_get_function_specifiers


def _cursor_get_first_child(self):
    child_holder = []

    def first_child_visitor(child, parent, holder):
        assert child != clang.cindex.conf.lib.clang_getNullCursor()
        assert parent != child
        # Create reference to TU so it isn't GC'd before Cursor.
        child._tu = self._tu
        holder.append(child)
        return 0  # CXChildVisit_Break

    clang.cindex.conf.lib.clang_visitChildren(
        self, clang.cindex.callbacks["cursor_visit"](first_child_visitor), child_holder
    )

    return None if len(child_holder) == 0 else child_holder[0]


Cursor.first_child = property(fget=_cursor_get_first_child)

__attr_flag_dict = {
    0x01: "readonly",
    0x02: "getter",
    0x04: "assign",
    0x08: "readwrite",
    0x10: "retain",
    0x20: "copy",
    0x40: "nonatomic",
    0x80: "setter",
    0x100: "atomic",
    0x200: "weak",
    0x400: "strong",
    0x800: "unsafe_unretained",
}


def _cursor_get_property_attributes(self):
    if self.kind != CursorKind.OBJC_PROPERTY_DECL:
        return None

    attr_flags = clang.cindex.conf.lib.clang_Cursor_getObjCPropertyAttributes(self, 0)

    attrs = set()
    for flag, attr in __attr_flag_dict.items():
        if attr_flags & flag:
            attrs.add(attr)

    if "getter" in attrs or "setter" in attrs:
        for string in self.objc_type_encoding.split(","):
            if string.startswith("G"):
                attrs.remove("getter")
                attrs.add(("getter", string[1:]))
            elif string.startswith("S"):
                attrs.remove("setter")
                attrs.add(("setter", string[1:]))

    return attrs


Cursor.get_property_attributes = _cursor_get_property_attributes

Cursor.canonical_distinct = property(
    fget=lambda self: None if self == self.canonical else self.canonical
)
Cursor.referenced_distinct = property(
    fget=lambda self: None
    if ((not self.referenced) or self == self.referenced)
    else self.referenced
)
Cursor.linkage = property(
    fget=lambda self: LinkageKind.from_id(
        clang.cindex.conf.lib.clang_getCursorLinkage(self)
    )
)
Cursor.is_virtual = property(
    fget=lambda self: clang.cindex.conf.lib.clang_CXXMethod_isVirtual(self)
)
Cursor.is_optional_for_protocol = property(
    fget=lambda self: bool(clang.cindex.conf.lib.clang_Cursor_isObjCOptional(self))
)
Cursor.is_variadic = property(
    fget=lambda self: bool(clang.cindex.conf.lib.clang_Cursor_isVariadic(self))
)
Cursor.type_valid = property(
    fget=lambda self: None if self.type.kind == TypeKind.INVALID else self.type
)
Cursor.result_type_valid = property(
    fget=lambda self: None
    if self.result_type.kind == TypeKind.INVALID
    else self.result_type
)


def _cursor_result_type(self):
    # See note above;  Yes, we are *replacing* the
    # implementation of result_type from libclang
    if not hasattr(self, "_result_type"):
        self._result_type = clang.cindex.conf.lib.clang_getCursorResultType(self)
    return self._result_type


Cursor.result_type = property(fget=_cursor_result_type)

__specifiers = ["public", "protected", "package", "private"]


def _cursor_get_access_specifier(self):
    if self.kind != CursorKind.OBJC_IVAR_DECL:
        return None

    walker = self
    while (
        walker
        and walker.kind != CursorKind.OBJC_INTERFACE_DECL
        and walker.kind != CursorKind.OBJC_CATEGORY_DECL
    ):
        walker = walker.semantic_parent

    interface_decl = walker

    current_level = "protected"  # default
    last_token_was_at = False
    for token in interface_decl.get_tokens():
        tstr = token.spelling
        if not last_token_was_at and tstr == "@":
            last_token_was_at = True
        elif last_token_was_at:
            last_token_was_at = False
            if tstr in __specifiers:
                current_level = tstr
        elif self.spelling == tstr:
            return current_level

    return None


Cursor.access_specifier = property(fget=_cursor_get_access_specifier)


def _cursor_reconstitute_macro(self):
    if self.kind != CursorKind.MACRO_DEFINITION:
        return None

    # try to reconstitute by tokens...
    string = ""
    min_offset = self.extent.start.offset
    max_offset = self.extent.end.offset
    tokens = list(self.get_tokens())

    last_token = None
    for token in tokens:
        if (
            last_token
            and (token.extent.start.offset - last_token.extent.end.offset) >= 1
        ):
            string += " "
        if (
            token.extent.start.offset >= min_offset
            and token.extent.end.offset <= max_offset
        ):
            string += token.spelling
        last_token = token

    return string.rstrip(" ")


Cursor.reconstitute_macro = _cursor_reconstitute_macro


def _cursor_get_objc_decl_qualifiers(self):
    if self.kind not in [
        CursorKind.OBJC_CLASS_METHOD_DECL,
        CursorKind.OBJC_INSTANCE_METHOD_DECL,
        CursorKind.PARM_DECL,
    ]:
        return None
    val = clang.cindex.conf.lib.clang_Cursor_getObjCDeclQualifiers(self)
    return ObjcDeclQualifier(val)


Cursor.objc_decl_qualifiers = property(fget=_cursor_get_objc_decl_qualifiers)

Cursor.underlying_typedef_type_valid = property(
    fget=lambda self: self.underlying_typedef_type.valid_type
)


def _cursor_enum_value(self):
    """
    Replacing yet another broken implementation from the libclang python bindings
    @return:
    @rtype: int
    """
    if not hasattr(self, "_enum_value"):
        assert self.kind == CursorKind.ENUM_CONSTANT_DECL
        # Figure out the underlying type of the enum to know if it
        # is a signed or unsigned quantity.
        underlying_type = self.type.get_canonical()
        if underlying_type.kind == TypeKind.ENUM:
            underlying_type = (
                underlying_type.get_declaration().enum_type.get_canonical()
            )
        if underlying_type.kind in (
            TypeKind.CHAR_U,
            TypeKind.UCHAR,
            TypeKind.CHAR16,
            TypeKind.CHAR32,
            TypeKind.USHORT,
            TypeKind.UINT,
            TypeKind.ULONG,
            TypeKind.ULONGLONG,
            TypeKind.UINT128,
        ):
            self._enum_value = clang.cindex.conf.lib.clang_getEnumConstantDeclUnsignedValue(  # noqa: B950
                self
            )
        else:
            self._enum_value = clang.cindex.conf.lib.clang_getEnumConstantDeclValue(  # noqa: B950
                self
            )
    return self._enum_value


Cursor.enum_value = property(fget=_cursor_enum_value)


class NullabilityKind(BaseEnumeration):
    _kinds = []
    _name_map = None


NullabilityKind.NONNULL = NullabilityKind(0)
NullabilityKind.NULLABLE = NullabilityKind(1)
NullabilityKind.UNSPECIFIED = NullabilityKind(2)
NullabilityKind.INVALID = NullabilityKind(3)


def _type_nullability(self):
    if not hasattr(self, "_nullability"):
        self._nullability = clang.cindex.conf.lib.clang_Type_getNullability(self)

    return NullabilityKind.from_id(self._nullability)


Type.nullability = property(fget=_type_nullability)


def _type_modified_type(self):
    return clang.cindex.conf.lib.clang_Type_getModifiedType(self)


Type.modified_type = property(fget=_type_modified_type)


class Version(Structure):
    _fields_ = [("major", c_int), ("minor", c_int), ("subminor", c_int)]

    def __repr__(self):
        if self.subminor == -1:
            return f"{self.major}.{self.minor}"
        else:
            return f"{self.major}.{self.minor}.{self.subminor}"


class PlatformAvailability(Structure):
    """
    Availability information
    """

    _fields_ = [
        ("platform", _CXString),
        ("introduced", Version),
        ("deprecated", Version),
        ("obsoleted", Version),
        ("unavailable", c_int),
        ("message", _CXString),
    ]


clang.cindex.register_function(
    clang.cindex.conf.lib,
    (
        "clang_getCursorPlatformAvailability",
        [
            Cursor,
            POINTER(c_int),
            POINTER(_CXString),
            POINTER(c_int),
            POINTER(_CXString),
            POINTER(PlatformAvailability),
        ],
        c_int,
    ),
    False,
)

clang.cindex.register_function(
    clang.cindex.conf.lib,
    ("clang_disposeCXPlatformAvailability", [POINTER(PlatformAvailability)], None),
    False,
)


def _cursor_platform_availability(self):
    always_deprecated = c_int()
    deprecated_message = _CXString()
    always_unavailable = c_int()
    unavailable_message = _CXString()
    availability_size = 10
    availability = (PlatformAvailability * availability_size)()

    r = clang.cindex.conf.lib.clang_getCursorPlatformAvailability(
        self,
        byref(always_deprecated),
        byref(deprecated_message),
        byref(always_unavailable),
        byref(unavailable_message),
        availability,
        availability_size,
    )

    result = {
        "always_deprecated": bool(always_deprecated.value),
        "deprecated_message": _CXString.from_result(deprecated_message),
        "always_unavailable": bool(always_unavailable.value),
        "unavailable_message": _CXString.from_result(unavailable_message),
        "platform": {},
    }
    for i in range(r):
        result["platform"][_CXString.from_result(availability[i].platform)] = {
            "introduced": availability[i].introduced
            if availability[i].introduced.major != -1
            else None,
            "deprecated": availability[i].deprecated
            if availability[i].deprecated.major != -1
            else None,
            "obsoleted": availability[i].obsoleted
            if availability[i].obsoleted.major != -1
            else None,
            "unavailable": bool(availability[i].unavailable),
            "message": _CXString.from_result(availability[i].message),
        }

    return result


Cursor.platform_availability = property(fget=_cursor_platform_availability)
