# This is a heavily modified version of the clang Python bindings
import collections.abc
import ctypes
import enum
import itertools
import os
import typing

import objc


def c_char_p_to_string(value: bytes, fn: typing.Any, args: typing.Any) -> str:
    return value.decode()


def b(x: typing.Union[str, bytes]) -> bytes:  # Is this still needed?
    if isinstance(x, bytes):
        return x
    return x.encode("utf8")


# ctypes doesn't implicitly convert ctypes.c_void_p to the appropriate wrapper
# object. This is a problem, because it means that from_parameter will see an
# integer and pass the wrong value on platforms where int != void*. Work around
# this by marshalling object arguments as void**.
c_object_p = ctypes.POINTER(ctypes.c_void_p)


# Need better annotation for Callable
callbacks: typing.Dict[str, typing.Callable] = {}

# Exception Classes


class TranslationUnitLoadError(Exception):
    """Represents an error that occurred when loading a TranslationUnit.

    This is raised in the case where a TranslationUnit could not be
    instantiated due to failure in the libclang library.
    """

    pass


class SaveError(enum.IntEnum):
    # Indicates that an unknown error occurred. This typically indicates that
    # I/O failed during save.
    ERROR_UNKNOWN = 1

    # Indicates that errors during translation prevented saving. The errors
    # should be available via the TranslationUnit's diagnostics.
    ERROR_TRANSLATION_ERRORS = 2

    # Indicates that the translation unit was somehow invalid.
    ERROR_INVALID_TU = 3


class TranslationUnitSaveError(Exception):
    """Represents an error that occurred when saving a TranslationUnit.

    Each error has associated with it an enumerated value, accessible under
    e.save_error. Consumers can compare the value with one of the ERROR_
    constants in this class.
    """

    # Indicates that an unknown error occurred. This typically indicates that
    # I/O failed during save.
    ERROR_UNKNOWN = 1

    # Indicates that errors during translation prevented saving. The errors
    # should be available via the TranslationUnit's diagnostics.
    ERROR_TRANSLATION_ERRORS = 2

    # Indicates that the translation unit was somehow invalid.
    ERROR_INVALID_TU = 3

    def __init__(self, enumeration: int, message: str):
        self.save_error = SaveError(enumeration)
        Exception.__init__(self, "Error %d: %s" % (enumeration, message))


# Structures and Utility Classes


class CachedProperty(object):
    """Decorator that lazy-loads the value of a property.

    The first time the property is accessed, the original property function is
    executed. The value it returns is set as the new value of that instance's
    property, replacing the original method.
    """

    def __init__(self, wrapped):
        self.wrapped = wrapped
        try:
            self.__doc__ = wrapped.__doc__
        except AttributeError:
            pass

    def __get__(self, instance, instance_type=None):
        if instance is None:
            return self

        value = self.wrapped(instance)
        setattr(instance, self.wrapped.__name__, value)

        return value


class _CXString(ctypes.Structure):
    """Helper for transforming CXString results."""

    _fields_ = [("spelling", ctypes.c_char_p), ("free", ctypes.c_int)]

    def __del__(self):
        conf.lib.clang_disposeString(self)

    @staticmethod
    def from_result(
        res: "_CXString",
        fn: typing.Any = None,
        args: typing.Tuple[typing.Any, ...] = None,
    ) -> str:
        assert isinstance(res, _CXString)
        return conf.lib.clang_getCString(res)


class SourceLocation(ctypes.Structure):
    """
    A SourceLocation represents a particular location within a source file.
    """

    _fields_ = [("ptr_data", ctypes.c_void_p * 2), ("int_data", ctypes.c_uint)]
    _data = None

    def _get_instantiation(self):
        if self._data is None:
            f, l, c, o = c_object_p(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
            conf.lib.clang_getInstantiationLocation(
                self, ctypes.byref(f), ctypes.byref(l), ctypes.byref(c), ctypes.byref(o)
            )
            if f:
                f = File(f)
            else:
                f = None
            self._data = (f, int(l.value), int(c.value), int(o.value))
        return self._data

    @staticmethod
    def from_position(
        tu: "TranslationUnit", file: str, line: int, column: int
    ) -> "SourceLocation":
        """
        Retrieve the source location associated with a given file/line/column in
        a particular translation unit.
        """
        return conf.lib.clang_getLocation(tu, file, line, column)

    @staticmethod
    def from_offset(tu: "TranslationUnit", file: str, offset: int) -> "SourceLocation":
        """Retrieve a SourceLocation from a given character offset.

        tu -- TranslationUnit file belongs to
        file -- File instance to obtain offset from
        offset -- Integer character offset within file
        """
        return conf.lib.clang_getLocationForOffset(tu, file, offset)

    @property
    def file(self) -> "typing.Optional[File]":
        """Get the file represented by this source location."""
        return self._get_instantiation()[0]

    @property
    def line(self) -> int:
        """Get the line represented by this source location."""
        return self._get_instantiation()[1]

    @property
    def column(self) -> int:
        """Get the column represented by this source location."""
        return self._get_instantiation()[2]

    @property
    def offset(self) -> int:
        """Get the file offset represented by this source location."""
        return self._get_instantiation()[3]

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, SourceLocation):
            return False
        return conf.lib.clang_equalLocations(self, other)

    def __ne__(self, other: typing.Any) -> bool:
        return not self.__eq__(other)

    def __repr__(self) -> str:
        filename: typing.Optional[str]
        if self.file:
            filename = self.file.name
        else:
            filename = None
        return "<SourceLocation file %r, line %r, column %r>" % (
            filename,
            self.line,
            self.column,
        )


class SourceRange(ctypes.Structure):
    """
    A SourceRange describes a range of source locations within the source
    code.
    """

    _fields_ = [
        ("ptr_data", ctypes.c_void_p * 2),
        ("begin_int_data", ctypes.c_uint),
        ("end_int_data", ctypes.c_uint),
    ]

    @staticmethod
    def from_locations(start: SourceLocation, end: SourceLocation) -> "SourceRange":
        return conf.lib.clang_getRange(start, end)

    @property
    def start(self) -> SourceLocation:
        """
        Return a SourceLocation representing the first character within a
        source range.
        """
        return conf.lib.clang_getRangeStart(self)

    @property
    def end(self) -> SourceLocation:
        """
        Return a SourceLocation representing the last character within a
        source range.
        """
        return conf.lib.clang_getRangeEnd(self)

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, SourceRange):
            return False
        return conf.lib.clang_equalRanges(self, other)

    def __ne__(self, other: typing.Any) -> bool:
        return not self.__eq__(other)

    def __contains__(self, other: typing.Any) -> bool:
        """Useful to detect the Token/Lexer bug"""
        if not isinstance(other, SourceLocation):
            return False

        if other.file is None:
            if self.start.file is not None:
                return False

        elif other.file is not None:
            if self.start.file is None:
                return False

            assert self.end.file is not None

            if (
                self.start.file.name != other.file.name
                or other.file.name != self.end.file.name
            ):
                return False

        # same file, in between lines
        if self.start.line < other.line < self.end.line:
            return True
        elif self.start.line == other.line:
            # same file first line
            if self.start.column <= other.column:
                return True
        elif other.line == self.end.line:
            # same file last line
            if other.column <= self.end.column:
                return True
        return False

    def __repr__(self) -> str:
        return "<SourceRange start %r, end %r>" % (self.start, self.end)

    def get_raw_contents(self) -> typing.Optional[str]:
        if self.start.file is None or self.end.file is None:
            return None

        start_file_name = self.start.file.name
        end_file_name = self.end.file.name
        if start_file_name == end_file_name and os.path.exists(start_file_name):
            try:
                with open(start_file_name) as fp:
                    fp.seek(self.start.offset)
                    contents = fp.read(self.end.offset - self.start.offset)
            except UnicodeDecodeError:
                # Sigh... some headers contain text that isn't UTF-8
                with open(start_file_name, encoding="latin1") as fp:
                    fp.seek(self.start.offset)
                    contents = fp.read(self.end.offset - self.start.offset)

        return contents


class DiagnosticSeverity(enum.IntEnum):
    IGNORED = 0
    NOTE = 1
    WARNING = 2
    ERROR = 3
    FATAL = 4


class Diagnostic(object):
    """
    A Diagnostic is a single instance of a Clang diagnostic. It includes the
    diagnostic severity, the message, the location the diagnostic occurred, as
    well as additional source ranges and associated fix-it hints.
    """

    DisplaySourceLocation = 0x01
    DisplayColumn = 0x02
    DisplaySourceRanges = 0x04
    DisplayOption = 0x08
    DisplayCategoryId = 0x10
    DisplayCategoryName = 0x20
    _FormatOptionsMask = 0x3F

    def __init__(self, ptr):
        self.ptr = ptr

    def __del__(self):
        conf.lib.clang_disposeDiagnostic(self)

    @property
    def severity(self) -> DiagnosticSeverity:
        return DiagnosticSeverity(conf.lib.clang_getDiagnosticSeverity(self))

    @property
    def location(self):
        return conf.lib.clang_getDiagnosticLocation(self)

    @property
    def spelling(self) -> str:
        return conf.lib.clang_getDiagnosticSpelling(self)

    @property
    def ranges(self):
        class RangeIterator(object):
            def __init__(self, diag):
                self.diag = diag

            def __len__(self):
                return int(conf.lib.clang_getDiagnosticNumRanges(self.diag))

            def __getitem__(self, key):
                if key >= len(self):
                    raise IndexError
                return conf.lib.clang_getDiagnosticRange(self.diag, key)

        return RangeIterator(self)

    @property
    def fixits(self):
        class FixItIterator(object):
            def __init__(self, diag):
                self.diag = diag

            def __len__(self):
                return int(conf.lib.clang_getDiagnosticNumFixIts(self.diag))

            def __getitem__(self, key):
                range = SourceRange()
                value = conf.lib.clang_getDiagnosticFixIt(
                    self.diag, key, ctypes.byref(range)
                )
                if len(value) == 0:
                    raise IndexError

                return FixIt(range, value)

        return FixItIterator(self)

    @property
    def children(self):
        class ChildDiagnosticsIterator(object):
            def __init__(self, diag):
                self.diag_set = conf.lib.clang_getChildDiagnostics(diag)

            def __len__(self):
                return int(conf.lib.clang_getNumDiagnosticsInSet(self.diag_set))

            def __getitem__(self, key):
                diag = conf.lib.clang_getDiagnosticInSet(self.diag_set, key)
                if not diag:
                    raise IndexError
                return Diagnostic(diag)

        return ChildDiagnosticsIterator(self)

    @property
    def category_number(self):
        """The category number for this diagnostic or 0 if unavailable."""
        return conf.lib.clang_getDiagnosticCategory(self)

    @property
    def category_name(self):
        """The string name of the category for this diagnostic."""
        return conf.lib.clang_getDiagnosticCategoryText(self)

    @property
    def option(self):
        """The command-line option that enables this diagnostic."""
        return conf.lib.clang_getDiagnosticOption(self, None)

    @property
    def disable_option(self):
        """The command-line option that disables this diagnostic."""
        disable = _CXString()
        conf.lib.clang_getDiagnosticOption(self, ctypes.byref(disable))
        return _CXString.from_result(disable)

    def format(self, options=None):
        """
        Format this diagnostic for display. The options argument takes
        Diagnostic.Display* flags, which can be combined using bitwise OR. If
        the options argument is not provided, the default display options will
        be used.
        """
        if options is None:
            options = conf.lib.clang_defaultDiagnosticDisplayOptions()
        if options & ~Diagnostic._FormatOptionsMask:
            raise ValueError("Invalid format options")
        return conf.lib.clang_formatDiagnostic(self, options)

    def __repr__(self):
        return "<Diagnostic severity %r, location %r, spelling %r>" % (
            self.severity,
            self.location,
            self.spelling,
        )

    def __str__(self):
        return self.format()

    def from_param(self):
        return self.ptr


class FixIt(object):
    """
    A FixIt represents a transformation to be applied to the source to
    "fix-it". The fix-it shouldbe applied by replacing the given source range
    with the given value.
    """

    def __init__(self, range, value):
        self.range = range
        self.value = value

    def __repr__(self):
        return "<FixIt range %r, value %r>" % (self.range, self.value)


class TokenGroup(object):
    """Helper class to facilitate token management.

    Tokens are allocated from libclang in chunks. They must be disposed of as a
    collective group.

    One purpose of this class is for instances to represent groups of allocated
    tokens. Each token in a group contains a reference back to an instance of
    this class. When all tokens from a group are garbage collected, it allows
    this class to be garbage collected. When this class is garbage collected,
    it calls the libclang destructor which invalidates all tokens in the group.

    You should not instantiate this class outside of this module.
    """

    def __init__(self, tu, memory, count):
        self._tu = tu
        self._memory = memory
        self._count = count

    def __del__(self):
        conf.lib.clang_disposeTokens(self._tu, self._memory, self._count)

    @staticmethod
    def get_tokens(tu, extent):
        """Helper method to return all tokens in an extent.

        This functionality is needed multiple places in this module. We define
        it here because it seems like a logical place.
        """
        tokens_memory = ctypes.POINTER(Token)()
        tokens_count = ctypes.c_uint()

        conf.lib.clang_tokenize(
            tu, extent, ctypes.byref(tokens_memory), ctypes.byref(tokens_count)
        )

        count = int(tokens_count.value)

        # If we get no tokens, no memory was allocated. Be sure not to return
        # anything and potentially call a destructor on nothing.
        if count < 1:
            return

        tokens_array = ctypes.cast(
            tokens_memory, ctypes.POINTER(Token * count)
        ).contents

        token_group = TokenGroup(tu, tokens_memory, tokens_count)

        for i in range(0, count):
            token = Token()
            token.int_data = tokens_array[i].int_data
            token.ptr_data = tokens_array[i].ptr_data
            token._tu = tu
            token._group = token_group

            yield token


class TokenKind(object):
    """Describes a specific type of a Token."""

    _value_map: typing.Dict[int, "TokenKind"] = {}  # int -> TokenKind

    def __init__(self, value, name):
        """Create a new TokenKind instance from a numeric value and a name."""
        self.value = value
        self.name = name

    def __repr__(self):
        return "TokenKind.%s" % (self.name,)

    @staticmethod
    def from_value(value):
        """Obtain a registered TokenKind instance from its value."""
        result = TokenKind._value_map.get(value, None)

        if result is None:
            raise ValueError("Unknown TokenKind: %d" % value)

        return result

    @staticmethod
    def register(value, name):
        """Register a new TokenKind enumeration.

        This should only be called at module load time by code within this
        package.
        """
        if value in TokenKind._value_map:
            raise ValueError("TokenKind already registered: %d" % value)

        kind = TokenKind(value, name)
        TokenKind._value_map[value] = kind
        setattr(TokenKind, name, kind)


class CursorKind(enum.IntEnum):
    """
    A CursorKind describes the kind of entity that a cursor points to.
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    @classmethod
    def get_all_kinds(cls):
        """Return all CursorKind enumeration instances."""
        return list(cls)

    def is_declaration(self):
        """Test if this is a declaration kind."""
        return conf.lib.clang_isDeclaration(self)

    def is_reference(self):
        """Test if this is a reference kind."""
        return conf.lib.clang_isReference(self)

    def is_expression(self):
        """Test if this is an expression kind."""
        return conf.lib.clang_isExpression(self)

    def is_statement(self):
        """Test if this is a statement kind."""
        return conf.lib.clang_isStatement(self)

    def is_attribute(self):
        """Test if this is an attribute kind."""
        return conf.lib.clang_isAttribute(self)

    def is_invalid(self):
        """Test if this is an invalid kind."""
        return conf.lib.clang_isInvalid(self)

    def is_translation_unit(self):
        """Test if this is a translation unit kind."""
        return conf.lib.clang_isTranslationUnit(self)

    def is_preprocessing(self):
        """Test if this is a preprocessing kind."""
        return conf.lib.clang_isPreprocessing(self)

    def is_unexposed(self):
        """Test if this is an unexposed kind."""
        return conf.lib.clang_isUnexposed(self)

    # definitions, etc. However, the specific kind of the declaration is not
    # reported.
    UNEXPOSED_DECL = 1

    # A C or C++ struct.
    STRUCT_DECL = 2

    # A C or C++ union.
    UNION_DECL = 3

    # A C++ class.
    CLASS_DECL = 4

    # An enumeration.
    ENUM_DECL = 5

    # A field (in C) or non-static data member (in C++) in a struct, union, or C++
    # class.
    FIELD_DECL = 6

    # An enumerator constant.
    ENUM_CONSTANT_DECL = 7

    # A function.
    FUNCTION_DECL = 8

    # A variable.
    VAR_DECL = 9

    # A function or method parameter.
    PARM_DECL = 10

    # An Objective-C @interface.
    OBJC_INTERFACE_DECL = 11

    # An Objective-C @interface for a category.
    OBJC_CATEGORY_DECL = 12

    # An Objective-C @protocol declaration.
    OBJC_PROTOCOL_DECL = 13

    # An Objective-C @property declaration.
    OBJC_PROPERTY_DECL = 14

    # An Objective-C instance variable.
    OBJC_IVAR_DECL = 15

    # An Objective-C instance method.
    OBJC_INSTANCE_METHOD_DECL = 16

    # An Objective-C class method.
    OBJC_CLASS_METHOD_DECL = 17

    # An Objective-C @implementation.
    OBJC_IMPLEMENTATION_DECL = 18

    # An Objective-C @implementation for a category.
    OBJC_CATEGORY_IMPL_DECL = 19

    # A typedef.
    TYPEDEF_DECL = 20

    # A C++ class method.
    CXX_METHOD = 21

    # A C++ namespace.
    NAMESPACE = 22

    # A linkage specification, e.g. 'extern "C"'.
    LINKAGE_SPEC = 23

    # A C++ constructor.
    CONSTRUCTOR = 24

    # A C++ destructor.
    DESTRUCTOR = 25

    # A C++ conversion function.
    CONVERSION_FUNCTION = 26

    # A C++ template type parameter
    TEMPLATE_TYPE_PARAMETER = 27

    # A C++ non-type template parameter.
    TEMPLATE_NON_TYPE_PARAMETER = 28

    # A C++ template template parameter.
    TEMPLATE_TEMPLATE_PARAMETER = 29

    # A C++ function template.
    FUNCTION_TEMPLATE = 30

    # A C++ class template.
    CLASS_TEMPLATE = 31

    # A C++ class template partial specialization.
    CLASS_TEMPLATE_PARTIAL_SPECIALIZATION = 32

    # A C++ namespace alias declaration.
    NAMESPACE_ALIAS = 33

    # A C++ using directive
    USING_DIRECTIVE = 34

    # A C++ using declaration
    USING_DECLARATION = 35

    # A Type alias decl.
    TYPE_ALIAS_DECL = 36

    # A Objective-C synthesize decl
    OBJC_SYNTHESIZE_DECL = 37

    # A Objective-C dynamic decl
    OBJC_DYNAMIC_DECL = 38

    # A C++ access specifier decl.
    CXX_ACCESS_SPEC_DECL = 39

    # Reference Kinds

    OBJC_SUPER_CLASS_REF = 40
    OBJC_PROTOCOL_REF = 41
    OBJC_CLASS_REF = 42

    # A reference to a type declaration.
    #
    # A type reference occurs anywhere where a type is named but not
    # declared. For example, given:
    #   typedef unsigned size_type;
    #   size_type size;
    #
    # The typedef is a declaration of size_type (CXCursor_TypedefDecl),
    # while the type of the variable "size" is referenced. The cursor
    # referenced by the type of size is the typedef for size_type.
    TYPE_REF = 43
    CXX_BASE_SPECIFIER = 44

    # A reference to a class template, function template, template
    # template parameter, or class template partial specialization.
    TEMPLATE_REF = 45

    # A reference to a namespace or namepsace alias.
    NAMESPACE_REF = 46

    # A reference to a member of a struct, union, or class that occurs in
    # some non-expression context, e.g., a designated initializer.
    MEMBER_REF = 47

    # A reference to a labeled statement.
    LABEL_REF = 48

    # A reference to a set of overloaded functions or function templates
    # that has not yet been resolved to a specific function or function template.
    OVERLOADED_DECL_REF = 49

    # A reference to a variable that occurs in some non-expression
    # context, e.g., a C++ lambda capture list.
    VARIABLE_REF = 50

    # Invalid/Error Kinds

    INVALID_FILE = 70
    NO_DECL_FOUND = 71
    NOT_IMPLEMENTED = 72
    INVALID_CODE = 73

    # Expression Kinds

    # An expression whose specific kind is not exposed via this interface.
    #
    # Unexposed expressions have the same operations as any other kind of
    # expression; one can extract their location information, spelling, children,
    # etc. However, the specific kind of the expression is not reported.
    UNEXPOSED_EXPR = 100

    # An expression that refers to some value declaration, such as a function,
    # variable, or enumerator.
    DECL_REF_EXPR = 101

    # An expression that refers to a member of a struct, union, class, Objective-C
    # class, etc.
    MEMBER_REF_EXPR = 102

    # An expression that calls a function.
    CALL_EXPR = 103

    # An expression that sends a message to an Objective-C object or class.
    OBJC_MESSAGE_EXPR = 104

    # An expression that represents a block literal.
    BLOCK_EXPR = 105

    # An integer literal.
    INTEGER_LITERAL = 106

    # A floating point number literal.
    FLOATING_LITERAL = 107

    # An imaginary number literal.
    IMAGINARY_LITERAL = 108

    # A string literal.
    STRING_LITERAL = 109

    # A character literal.
    CHARACTER_LITERAL = 110

    # A parenthesized expression, e.g. "(1)".
    #
    # This AST node is only formed if full location information is requested.
    PAREN_EXPR = 111

    # This represents the unary-expression's (except sizeof and
    # alignof).
    UNARY_OPERATOR = 112

    # [C99 6.5.2.1] Array Subscripting.
    ARRAY_SUBSCRIPT_EXPR = 113

    # A builtin binary operation expression such as "x + y" or
    # "x <= y".
    BINARY_OPERATOR = 114

    # Compound assignment such as "+=".
    COMPOUND_ASSIGNMENT_OPERATOR = 115

    # The ?: ternary operator.
    CONDITIONAL_OPERATOR = 116

    # An explicit cast in C (C99 6.5.4) or a C-style cast in C++
    # (C++ [expr.cast]), which uses the syntax (Type)expr.
    #
    # For example: (int)f.
    CSTYLE_CAST_EXPR = 117

    # [C99 6.5.2.5]
    COMPOUND_LITERAL_EXPR = 118

    # Describes an C or C++ initializer list.
    INIT_LIST_EXPR = 119

    # The GNU address of label extension, representing &&label.
    ADDR_LABEL_EXPR = 120

    # This is the GNU Statement Expression extension: ({int X=4; X;})
    STMT_EXPR = 121

    # Represents a C11 generic selection.
    GENERIC_SELECTION_EXPR = 122

    # Implements the GNU __null extension, which is a name for a null
    # pointer constant that has integral type (e.g., int or long) and is the same
    # size and alignment as a pointer.
    #
    # The __null extension is typically only used by system headers, which define
    # NULL as __null in C++ rather than using 0 (which is an integer that may not
    # match the size of a pointer).
    GNU_NULL_EXPR = 123

    # C++'s static_cast<> expression.
    CXX_STATIC_CAST_EXPR = 124

    # C++'s dynamic_cast<> expression.
    CXX_DYNAMIC_CAST_EXPR = 125

    # C++'s reinterpret_cast<> expression.
    CXX_REINTERPRET_CAST_EXPR = 126

    # C++'s const_cast<> expression.
    CXX_CONST_CAST_EXPR = 127

    # Represents an explicit C++ type conversion that uses "functional"
    # notion (C++ [expr.type.conv]).
    #
    # Example:
    # \code
    #   x = int(0.5);
    # \endcode
    CXX_FUNCTIONAL_CAST_EXPR = 128

    # A C++ typeid expression (C++ [expr.typeid]).
    CXX_TYPEID_EXPR = 129

    # [C++ 2.13.5] C++ Boolean Literal.
    CXX_BOOL_LITERAL_EXPR = 130

    # [C++0x 2.14.7] C++ Pointer Literal.
    CXX_NULL_PTR_LITERAL_EXPR = 131

    # Represents the "this" expression in C++
    CXX_THIS_EXPR = 132

    # [C++ 15] C++ Throw Expression.
    #
    # This handles 'throw' and 'throw' assignment-expression. When
    # assignment-expression isn't present, Op will be null.
    CXX_THROW_EXPR = 133

    # A new expression for memory allocation and constructor calls, e.g:
    # "new CXXNewExpr(foo)".
    CXX_NEW_EXPR = 134

    # A delete expression for memory deallocation and destructor calls,
    # e.g. "delete[] pArray".
    CXX_DELETE_EXPR = 135

    # Represents a unary expression.
    CXX_UNARY_EXPR = 136

    # ObjCStringLiteral, used for Objective-C string literals i.e. "foo".
    OBJC_STRING_LITERAL = 137

    # ObjCEncodeExpr, used for in Objective-C.
    OBJC_ENCODE_EXPR = 138

    # ObjCSelectorExpr used for in Objective-C.
    OBJC_SELECTOR_EXPR = 139

    # Objective-C's protocol expression.
    OBJC_PROTOCOL_EXPR = 140

    # An Objective-C "bridged" cast expression, which casts between
    # Objective-C pointers and C pointers, transferring ownership in the process.
    #
    # \code
    #   NSString *str = (__bridge_transfer NSString *)CFCreateString();
    # \endcode
    OBJC_BRIDGE_CAST_EXPR = 141

    # Represents a C++0x pack expansion that produces a sequence of
    # expressions.
    #
    # A pack expansion expression contains a pattern (which itself is an
    # expression) followed by an ellipsis. For example:
    PACK_EXPANSION_EXPR = 142

    # Represents an expression that computes the length of a parameter
    # pack.
    SIZE_OF_PACK_EXPR = 143

    # Represents a C++ lambda expression that produces a local function
    # object.
    #
    #  \code
    #  void abssort(float *x, unsigned N) {
    #    std::sort(x, x + N,
    #              [](float a, float b) {
    #                return std::abs(a) < std::abs(b);
    #              });
    #  }
    #  \endcode
    LAMBDA_EXPR = 144

    # Objective-c Boolean Literal.
    OBJ_BOOL_LITERAL_EXPR = 145

    # Represents the "self" expression in a ObjC method.
    OBJ_SELF_EXPR = 146

    # OpenMP 4.0 [2.4, Array Section].
    OMP_ARRAY_SECTION_EXPR = 147

    # Represents an @available(...) check.
    OBJC_AVAILABILITY_CHECK_EXPR = 148

    # A statement whose specific kind is not exposed via this interface.
    #
    # Unexposed statements have the same operations as any other kind of statement;
    # one can extract their location information, spelling, children, etc. However,
    # the specific kind of the statement is not reported.
    UNEXPOSED_STMT = 200

    # A labelled statement in a function.
    LABEL_STMT = 201

    # A compound statement
    COMPOUND_STMT = 202

    # A case statement.
    CASE_STMT = 203

    # A default statement.
    DEFAULT_STMT = 204

    # An if statement.
    IF_STMT = 205

    # A switch statement.
    SWITCH_STMT = 206

    # A while statement.
    WHILE_STMT = 207

    # A do statement.
    DO_STMT = 208

    # A for statement.
    FOR_STMT = 209

    # A goto statement.
    GOTO_STMT = 210

    # An indirect goto statement.
    INDIRECT_GOTO_STMT = 211

    # A continue statement.
    CONTINUE_STMT = 212

    # A break statement.
    BREAK_STMT = 213

    # A return statement.
    RETURN_STMT = 214

    # A GNU-style inline assembler statement.
    ASM_STMT = 215

    # Objective-C's overall @try-@catch-@finally statement.
    OBJC_AT_TRY_STMT = 216

    # Objective-C's @catch statement.
    OBJC_AT_CATCH_STMT = 217

    # Objective-C's @finally statement.
    OBJC_AT_FINALLY_STMT = 218

    # Objective-C's @throw statement.
    OBJC_AT_THROW_STMT = 219

    # Objective-C's @synchronized statement.
    OBJC_AT_SYNCHRONIZED_STMT = 220

    # Objective-C's autorealease pool statement.
    OBJC_AUTORELEASE_POOL_STMT = 221

    # Objective-C's for collection statement.
    OBJC_FOR_COLLECTION_STMT = 222

    # C++'s catch statement.
    CXX_CATCH_STMT = 223

    # C++'s try statement.
    CXX_TRY_STMT = 224

    # C++'s for (* : *) statement.
    CXX_FOR_RANGE_STMT = 225

    # Windows ctypes.Structured Exception Handling's try statement.
    SEH_TRY_STMT = 226

    # Windows ctypes.Structured Exception Handling's except statement.
    SEH_EXCEPT_STMT = 227

    # Windows ctypes.Structured Exception Handling's finally statement.
    SEH_FINALLY_STMT = 228

    # A MS inline assembly statement extension.
    MS_ASM_STMT = 229

    # The null statement.
    NULL_STMT = 230

    # Adaptor class for mixing declarations with statements and expressions.
    DECL_STMT = 231

    # OpenMP parallel directive.
    OMP_PARALLEL_DIRECTIVE = 232

    # OpenMP SIMD directive.
    OMP_SIMD_DIRECTIVE = 233

    # OpenMP for directive.
    OMP_FOR_DIRECTIVE = 234

    # OpenMP sections directive.
    OMP_SECTIONS_DIRECTIVE = 235

    # OpenMP section directive.
    OMP_SECTION_DIRECTIVE = 236

    # OpenMP single directive.
    OMP_SINGLE_DIRECTIVE = 237

    # OpenMP parallel for directive.
    OMP_PARALLEL_FOR_DIRECTIVE = 238

    # OpenMP parallel sections directive.
    OMP_PARALLEL_SECTIONS_DIRECTIVE = 239

    # OpenMP task directive.
    OMP_TASK_DIRECTIVE = 240

    # OpenMP master directive.
    OMP_MASTER_DIRECTIVE = 241

    # OpenMP critical directive.
    OMP_CRITICAL_DIRECTIVE = 242

    # OpenMP taskyield directive.
    OMP_TASKYIELD_DIRECTIVE = 243

    # OpenMP barrier directive.
    OMP_BARRIER_DIRECTIVE = 244

    # OpenMP taskwait directive.
    OMP_TASKWAIT_DIRECTIVE = 245

    # OpenMP flush directive.
    OMP_FLUSH_DIRECTIVE = 246

    # Windows ctypes.Structured Exception Handling's leave statement.
    SEH_LEAVE_STMT = 247

    # OpenMP ordered directive.
    OMP_ORDERED_DIRECTIVE = 248

    # OpenMP atomic directive.
    OMP_ATOMIC_DIRECTIVE = 249

    # OpenMP for SIMD directive.
    OMP_FOR_SIMD_DIRECTIVE = 250

    # OpenMP parallel for SIMD directive.
    OMP_PARALLELFORSIMD_DIRECTIVE = 251

    # OpenMP target directive.
    OMP_TARGET_DIRECTIVE = 252

    # OpenMP teams directive.
    OMP_TEAMS_DIRECTIVE = 253

    # OpenMP taskgroup directive.
    OMP_TASKGROUP_DIRECTIVE = 254

    # OpenMP cancellation point directive.
    OMP_CANCELLATION_POINT_DIRECTIVE = 255

    # OpenMP cancel directive.
    OMP_CANCEL_DIRECTIVE = 256

    # OpenMP target data directive.
    OMP_TARGET_DATA_DIRECTIVE = 257

    # OpenMP taskloop directive.
    OMP_TASK_LOOP_DIRECTIVE = 258

    # OpenMP taskloop simd directive.
    OMP_TASK_LOOP_SIMD_DIRECTIVE = 259

    # OpenMP distribute directive.
    OMP_DISTRIBUTE_DIRECTIVE = 260

    # OpenMP target enter data directive.
    OMP_TARGET_ENTER_DATA_DIRECTIVE = 261

    # OpenMP target exit data directive.
    OMP_TARGET_EXIT_DATA_DIRECTIVE = 262

    # OpenMP target parallel directive.
    OMP_TARGET_PARALLEL_DIRECTIVE = 263

    # OpenMP target parallel for directive.
    OMP_TARGET_PARALLELFOR_DIRECTIVE = 264

    # OpenMP target update directive.
    OMP_TARGET_UPDATE_DIRECTIVE = 265

    # OpenMP distribute parallel for directive.
    OMP_DISTRIBUTE_PARALLELFOR_DIRECTIVE = 266

    # OpenMP distribute parallel for simd directive.
    OMP_DISTRIBUTE_PARALLEL_FOR_SIMD_DIRECTIVE = 267

    # OpenMP distribute simd directive.
    OMP_DISTRIBUTE_SIMD_DIRECTIVE = 268

    # OpenMP target parallel for simd directive.
    OMP_TARGET_PARALLEL_FOR_SIMD_DIRECTIVE = 269

    # OpenMP target simd directive.
    OMP_TARGET_SIMD_DIRECTIVE = 270

    # OpenMP teams distribute directive.
    OMP_TEAMS_DISTRIBUTE_DIRECTIVE = 271

    # Other Kinds

    # Cursor that represents the translation unit itself.
    #
    # The translation unit cursor exists primarily to act as the root cursor for
    # traversing the contents of a translation unit.
    TRANSLATION_UNIT = 300

    # Attributes

    # An attribute whoe specific kind is note exposed via this interface
    UNEXPOSED_ATTR = 400

    IB_ACTION_ATTR = 401
    IB_OUTLET_ATTR = 402
    IB_OUTLET_COLLECTION_ATTR = 403

    CXX_FINAL_ATTR = 404
    CXX_OVERRIDE_ATTR = 405
    ANNOTATE_ATTR = 406
    ASM_LABEL_ATTR = 407
    PACKED_ATTR = 408
    PURE_ATTR = 409
    CONST_ATTR = 410
    NODUPLICATE_ATTR = 411
    CUDACONSTANT_ATTR = 412
    CUDADEVICE_ATTR = 413
    CUDAGLOBAL_ATTR = 414
    CUDAHOST_ATTR = 415
    CUDASHARED_ATTR = 416

    VISIBILITY_ATTR = 417

    DLLEXPORT_ATTR = 418
    DLLIMPORT_ATTR = 419
    NSRETURNSRETAINED_ATTR = 420
    NSRETURNSNOTRETAINED_ATTR = 421
    NSRETURNSAUTORELEASED_ATTR = 422
    NSCONSUMESSELF_ATTR = 423
    NSCONSUMED_ATTR = 424
    OBJCEXCEPTION_ATTR = 425
    OBJCNSOBJECT_ATTR = 426
    OBJCINDEPENDENTCLASS_ATTR = 427
    OBJCPRESISELIVETIME_ATTR = 428
    OBJCRETURNSINNERPOINTER_ATTR = 429
    REQUIRESSUPER_ATTR = 430
    OBJCROOTCLASS_ATTR = 431
    OBJCSUBCLASSINGRESTRICTED_ATTR = 432
    EXPLICITPROTOCOLIMPL_ATTR = 433
    OBJCDESIGNATEDINITIALIZER_ATTR = 434
    OBJCBOXABLE_ATTR = 436
    FLAGENUM_ATTR = 437
    CONVERGENT_ATTR = 438
    WARN_UNUSED_ATTR = 439
    WARN_UNUSED_RESULT_ATTR = 440
    ALIGNED_ATTR = 441

    # Preprocessing
    PREPROCESSING_DIRECTIVE = 500
    MACRO_DEFINITION = 501
    MACRO_INSTANTIATION = 502
    INCLUSION_DIRECTIVE = 503

    # Extra declaration

    # A module import declaration.
    MODULE_IMPORT_DECL = 600
    # A type alias template declaration
    TYPE_ALIAS_TEMPLATE_DECL = 601
    # A static_assert or _Static_assert node
    STATIC_ASSERT = 602
    # A friend declaration
    FRIEND_DECL = 603

    # A code completion overload candidate.
    OVERLOAD_CANDIDATE = 700


# Template Argument Kinds
class TemplateArgumentKind(enum.IntEnum):
    """
    A TemplateArgumentKind describes the kind of entity that a template argument
    represents.
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    NULL = 0
    TYPE = 1
    DECLARATION = 2
    NULLPTR = 3
    INTEGRAL = 4


# Exception Specification Kinds
class ExceptionSpecificationKind(enum.IntEnum):
    """
    An ExceptionSpecificationKind describes the kind of exception specification
    that a function has.
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    NONE = 0
    DYNAMIC_NONE = 1
    DYNAMIC = 2
    MS_ANY = 3
    BASIC_NOEXCEPT = 4
    COMPUTED_NOEXCEPT = 5
    UNEVALUATED = 6
    UNINSTANTIATED = 7
    UNPARSED = 8


class ObjCDeclQualifier(enum.IntFlag):
    IN = 0x1
    INOUT = 0x2
    OUT = 0x4
    BYCOPY = 0x8
    BYREF = 0x10
    ONEWAY = 0x20

    @staticmethod
    def from_encode_string(string):
        val = ObjCDeclQualifier(0)
        string = string or ""
        for value, encode_string in _val_to_encode_string.items():
            if encode_string in string:
                val = val | value
        return val

    def to_encode_string(self):
        string = ""
        for value, encode_string in _val_to_encode_string.items():
            if self & value:
                string = string + encode_string
        return string


_val_to_encode_string = {
    ObjCDeclQualifier.IN: objc._C_IN,
    ObjCDeclQualifier.INOUT: objc._C_INOUT,
    ObjCDeclQualifier.OUT: objc._C_OUT,
    ObjCDeclQualifier.BYCOPY: objc._C_BYCOPY,
    ObjCDeclQualifier.BYREF: b"R",  # objc._C_BYREF,
    ObjCDeclQualifier.ONEWAY: objc._C_ONEWAY,
}


class Version(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_int),
        ("minor", ctypes.c_int),
        ("subminor", ctypes.c_int),
    ]

    def __repr__(self):
        if self.subminor == -1:
            return f"{self.major}.{self.minor}"
        else:
            return f"{self.major}.{self.minor}.{self.subminor}"


class PlatformAvailability(ctypes.Structure):
    """
    Availability information
    """

    _fields_ = [
        ("platform", _CXString),
        ("introduced", Version),
        ("deprecated", Version),
        ("obsoleted", Version),
        ("unavailable", ctypes.c_int),
        ("message", _CXString),
    ]


# Cursors


class Cursor(ctypes.Structure):
    """
    The Cursor class represents a reference to an element within the AST. It
    acts as a kind of iterator.
    """

    _fields_ = [
        ("_kind_id", ctypes.c_int),
        ("xdata", ctypes.c_int),
        ("data", ctypes.c_void_p * 3),
    ]

    @staticmethod
    def from_location(tu, location):
        # We store a reference to the TU in the instance so the TU won't get
        # collected before the cursor.
        cursor = conf.lib.clang_getCursor(tu, location)
        cursor._tu = tu

        return cursor

    def __hash__(self):
        return hash((self._kind_id, self.xdata, tuple(self.data)))

    def __eq__(self, other):
        return conf.lib.clang_equalCursors(self, other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def is_definition(self):
        """
        Returns true if the declaration pointed at by the cursor is also a
        definition of that entity.
        """
        return conf.lib.clang_isCursorDefinition(self)

    def is_const_method(self):
        """Returns True if the cursor refers to a C++ member function or member
        function template that is declared 'const'.
        """
        return conf.lib.clang_CXXMethod_isConst(self)

    def is_converting_constructor(self):
        """Returns True if the cursor refers to a C++ converting constructor."""
        return conf.lib.clang_CXXConstructor_isConvertingConstructor(self)

    def is_copy_constructor(self):
        """Returns True if the cursor refers to a C++ copy constructor."""
        return conf.lib.clang_CXXConstructor_isCopyConstructor(self)

    def is_default_constructor(self):
        """Returns True if the cursor refers to a C++ default constructor."""
        return conf.lib.clang_CXXConstructor_isDefaultConstructor(self)

    def is_move_constructor(self):
        """Returns True if the cursor refers to a C++ move constructor."""
        return conf.lib.clang_CXXConstructor_isMoveConstructor(self)

    def is_default_method(self):
        """Returns True if the cursor refers to a C++ member function or member
        function template that is declared '= default'.
        """
        return conf.lib.clang_CXXMethod_isDefaulted(self)

    def is_mutable_field(self):
        """Returns True if the cursor refers to a C++ field that is declared
        'mutable'.
        """
        return conf.lib.clang_CXXField_isMutable(self)

    def is_pure_virtual_method(self):
        """Returns True if the cursor refers to a C++ member function or member
        function template that is declared pure virtual.
        """
        return conf.lib.clang_CXXMethod_isPureVirtual(self)

    def is_static_method(self):
        """Returns True if the cursor refers to a C++ member function or member
        function template that is declared 'static'.
        """
        return conf.lib.clang_CXXMethod_isStatic(self)

    def is_virtual_method(self):
        """Returns True if the cursor refers to a C++ member function or member
        function template that is declared 'virtual'.
        """
        return conf.lib.clang_CXXMethod_isVirtual(self)

    def is_abstract_record(self):
        """Returns True if the cursor refers to a C++ record declaration
        that has pure virtual member functions.
        """
        return conf.lib.clang_CXXRecord_isAbstract(self)

    def is_scoped_enum(self):
        """Returns True if the cursor refers to a scoped enum declaration."""
        return conf.lib.clang_EnumDecl_isScoped(self)

    def get_definition(self):
        """
        If the cursor is a reference to a declaration or a declaration of
        some entity, return a cursor that points to the definition of that
        entity.
        """
        return conf.lib.clang_getCursorDefinition(self)

    def get_usr(self):
        """Return the Unified Symbol Resolution (USR) for the entity referenced
        by the given cursor (or None).

        A Unified Symbol Resolution (USR) is a string that identifies a
        particular entity (function, class, variable, etc.) within a
        program. USRs can be compared across translation units to determine,
        e.g., when references in one translation refer to an entity defined in
        another translation unit."""
        return conf.lib.clang_getCursorUSR(self)

    def get_included_file(self):
        """Returns the File that is included by the current inclusion cursor."""
        assert self.kind == CursorKind.INCLUSION_DIRECTIVE

        return conf.lib.clang_getIncludedFile(self)

    @property
    def kind(self):
        """Return the kind of this cursor."""
        return CursorKind.from_id(self._kind_id)

    @property
    def spelling(self):
        """Return the spelling of the entity pointed at by the cursor."""
        if not hasattr(self, "_spelling"):
            self._spelling = conf.lib.clang_getCursorSpelling(self)

        return self._spelling

    @property
    def displayname(self):
        """
        Return the display name for the entity referenced by this cursor.

        The display name contains extra information that helps identify the
        cursor, such as the parameters of a function or template or the
        arguments of a class template specialization.
        """
        if not hasattr(self, "_displayname"):
            self._displayname = conf.lib.clang_getCursorDisplayName(self)

        return self._displayname

    @property
    def mangled_name(self):
        """Return the mangled name for the entity referenced by this cursor."""
        if not hasattr(self, "_mangled_name"):
            self._mangled_name = conf.lib.clang_Cursor_getMangling(self)

        return self._mangled_name

    @property
    def location(self):
        """
        Return the source location (the starting character) of the entity
        pointed at by the cursor.
        """
        if not hasattr(self, "_loc"):
            self._loc = conf.lib.clang_getCursorLocation(self)

        return self._loc

    @property
    def linkage(self):
        """Return the linkage of this cursor."""
        if not hasattr(self, "_linkage"):
            self._linkage = conf.lib.clang_getCursorLinkage(self)

        return LinkageKind.from_id(self._linkage)

    @property
    def tls_kind(self):
        """Return the thread-local storage (TLS) kind of this cursor."""
        if not hasattr(self, "_tls_kind"):
            self._tls_kind = conf.lib.clang_getCursorTLSKind(self)

        return TLSKind.from_id(self._tls_kind)

    @property
    def extent(self):
        """
        Return the source range (the range of text) occupied by the entity
        pointed at by the cursor.
        """
        if not hasattr(self, "_extent"):
            self._extent = conf.lib.clang_getCursorExtent(self)

        return self._extent

    @property
    def storage_class(self):
        """
        Retrieves the storage class (if any) of the entity pointed at by the
        cursor.
        """
        if not hasattr(self, "_storage_class"):
            self._storage_class = conf.lib.clang_Cursor_getStorageClass(self)

        return StorageClass.from_id(self._storage_class)

    @property
    def availability(self):
        """
        Retrieves the availability of the entity pointed at by the cursor.
        """
        if not hasattr(self, "_availability"):
            self._availability = conf.lib.clang_getCursorAvailability(self)

        return AvailabilityKind.from_id(self._availability)

    @property
    def type(self):
        """
        Retrieve the Type (if any) of the entity pointed at by the cursor.
        """
        if not hasattr(self, "_type"):
            self._type = conf.lib.clang_getCursorType(self)

        return self._type

    @property
    def canonical(self):
        """Return the canonical Cursor corresponding to this Cursor.

        The canonical cursor is the cursor which is representative for the
        underlying entity. For example, if you have multiple forward
        declarations for the same class, the canonical cursor for the forward
        declarations will be identical.
        """
        if not hasattr(self, "_canonical"):
            self._canonical = conf.lib.clang_getCanonicalCursor(self)

        return self._canonical

    @property
    def exception_specification_kind(self):
        """
        Retrieve the exception specification kind, which is one of the values
        from the ExceptionSpecificationKind enumeration.
        """
        if not hasattr(self, "_exception_specification_kind"):
            exc_kind = conf.lib.clang_getCursorExceptionSpecificationType(self)
            self._exception_specification_kind = ExceptionSpecificationKind.from_id(
                exc_kind
            )

        return self._exception_specification_kind

    @property
    def underlying_typedef_type(self):
        """Return the underlying type of a typedef declaration.

        Returns a Type for the typedef this cursor is a declaration for. If
        the current cursor is not a typedef, this raises.
        """
        if not hasattr(self, "_underlying_type"):
            assert self.kind.is_declaration()
            self._underlying_type = conf.lib.clang_getTypedefDeclUnderlyingType(self)

        return self._underlying_type

    @property
    def enum_type(self):
        """Return the integer type of an enum declaration.

        Returns a Type corresponding to an integer. If the cursor is not for an
        enum, this raises.
        """
        if not hasattr(self, "_enum_type"):
            assert self.kind == CursorKind.ENUM_DECL
            self._enum_type = conf.lib.clang_getEnumDeclIntegerType(self)

        return self._enum_type

    @property
    def objc_type_encoding(self) -> bytes:
        """Return the Objective-C type encoding as a str."""
        if not hasattr(self, "_objc_type_encoding"):
            self._objc_type_encoding = conf.lib.clang_getDeclObjCTypeEncoding(self)

        return self._objc_type_encoding.encode()

    @property
    def hash(self):
        """Returns a hash of the cursor as an int."""
        if not hasattr(self, "_hash"):
            self._hash = conf.lib.clang_hashCursor(self)

        return self._hash

    @property
    def semantic_parent(self):
        """Return the semantic parent for this cursor."""
        if not hasattr(self, "_semantic_parent"):
            self._semantic_parent = conf.lib.clang_getCursorSemanticParent(self)

        return self._semantic_parent

    @property
    def lexical_parent(self):
        """Return the lexical parent for this cursor."""
        if not hasattr(self, "_lexical_parent"):
            self._lexical_parent = conf.lib.clang_getCursorLexicalParent(self)

        return self._lexical_parent

    @property
    def translation_unit(self):
        """Returns the TranslationUnit to which this Cursor belongs."""
        # If this triggers an AttributeError, the instance was not properly
        # created.
        return self._tu

    @property
    def referenced(self):
        """
        For a cursor that is a reference, returns a cursor
        representing the entity that it references.
        """
        if not hasattr(self, "_referenced"):
            self._referenced = conf.lib.clang_getCursorReferenced(self)

        return self._referenced

    @property
    def brief_comment(self):
        """Returns the brief comment text associated with that Cursor"""
        return conf.lib.clang_Cursor_getBriefCommentText(self)

    @property
    def raw_comment(self):
        """Returns the raw comment text associated with that Cursor"""
        return conf.lib.clang_Cursor_getRawCommentText(self)

    def get_arguments(self):
        """Return an iterator for accessing the arguments of this cursor."""
        num_args = conf.lib.clang_Cursor_getNumArguments(self)
        for i in range(0, num_args):
            yield conf.lib.clang_Cursor_getArgument(self, i)

    def get_num_template_arguments(self):
        """Returns the number of template args associated with this cursor."""
        return conf.lib.clang_Cursor_getNumTemplateArguments(self)

    def get_template_argument_kind(self, num):
        """Returns the TemplateArgumentKind for the indicated template
        argument."""
        return conf.lib.clang_Cursor_getTemplateArgumentKind(self, num)

    def get_template_argument_type(self, num):
        """Returns the CXType for the indicated template argument."""
        return conf.lib.clang_Cursor_getTemplateArgumentType(self, num)

    def get_template_argument_value(self, num):
        """Returns the value of the indicated arg as a signed 64b integer."""
        return conf.lib.clang_Cursor_getTemplateArgumentValue(self, num)

    def get_template_argument_unsigned_value(self, num):
        """Returns the value of the indicated arg as an unsigned 64b integer."""
        return conf.lib.clang_Cursor_getTemplateArgumentUnsignedValue(self, num)

    def has_child_of_kind(self, kind):
        for child in self.get_children():
            if child.kind == kind:
                return child

        return None

    def get_children(self):
        """Return an iterator for accessing the children of this cursor."""

        def visitor(child, parent, children):
            assert child != conf.lib.clang_getNullCursor()

            # Create reference to TU so it isn't GC'd before Cursor.
            child._tu = self._tu
            children.append(child)
            return 1  # continue

        children = []
        conf.lib.clang_visitChildren(self, callbacks["cursor_visit"](visitor), children)
        return iter(children)

    def walk_preorder(self):
        """Depth-first preorder walk over the cursor and its descendants.

        Yields cursors.
        """
        yield self
        for child in self.get_children():
            for descendant in child.walk_preorder():
                yield descendant

    def get_tokens(self):
        """Obtain Token instances formulating that compose this Cursor.

        This is a generator for Token instances. It returns all tokens which
        occupy the extent this cursor occupies.
        """
        return TokenGroup.get_tokens(self._tu, self.extent)

    def get_field_offsetof(self):
        """Returns the offsetof the FIELD_DECL pointed by this Cursor."""
        return conf.lib.clang_Cursor_getOffsetOfField(self)

    def is_anonymous(self):
        """
        Check if the record is anonymous.
        """
        if self.kind == CursorKind.FIELD_DECL:
            return self.type.get_declaration().is_anonymous()
        return conf.lib.clang_Cursor_isAnonymous(self)

    def is_bitfield(self):
        """
        Check if the field is a bitfield.
        """
        return conf.lib.clang_Cursor_isBitField(self)

    def get_bitfield_width(self):
        """
        Retrieve the width of a bitfield.
        """
        return conf.lib.clang_getFieldDeclBitWidth(self)

    @staticmethod
    def from_result(res, fn, args):
        assert isinstance(res, Cursor)
        if res == conf.lib.clang_getNullCursor():
            return None

        # Store a reference to the TU in the Python object so it won't get GC'd
        # before the Cursor.
        tu = None
        for arg in args:
            if isinstance(arg, TranslationUnit):
                tu = arg
                break

            if hasattr(arg, "translation_unit"):
                tu = arg.translation_unit
                break

        res._tu = tu
        return res

    @staticmethod
    def from_cursor_result(res, fn, args):
        assert isinstance(res, Cursor)
        if res == conf.lib.clang_getNullCursor():
            return None

        res._tu = args[0]._tu
        return res

    @property
    def access_specifier(self):
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
                if tstr in {"public", "protected", "package", "private"}:
                    current_level = tstr
            elif self.spelling == tstr:
                return current_level

        return None

    def reconstitute_macro(self):
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

    @property
    def objc_decl_qualifiers(self):
        if self.kind not in [
            CursorKind.OBJC_CLASS_METHOD_DECL,
            CursorKind.OBJC_INSTANCE_METHOD_DECL,
            CursorKind.PARM_DECL,
        ]:
            return None
        val = conf.lib.clang_Cursor_getObjCDeclQualifiers(self)
        return ObjCDeclQualifier(val)

    @property
    def underlying_typedef_type_valid(self):
        return self.underlying_typedef_type.valid_type

    @property
    def enum_value(self):
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
                self._enum_value = conf.lib.clang_getEnumConstantDeclUnsignedValue(
                    self
                )  # noqa: B950
            else:
                self._enum_value = conf.lib.clang_getEnumConstantDeclValue(
                    self
                )  # noqa: B950
        return self._enum_value

    def get_category_class_cursor(self):
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

    def get_category_class_name(self):
        category_class_cursor = self.get_category_class_cursor()
        return (
            None
            if category_class_cursor is None
            else getattr(category_class_cursor.type, "spelling", None)
        )

    def get_category_name(self):
        if self.kind == CursorKind.OBJC_CATEGORY_DECL:
            return self.spelling
        else:
            return None

    def get_is_informal_protocol(self):
        if self.kind == CursorKind.OBJC_CATEGORY_DECL:
            return (
                self.get_category_class_name() == "NSObject"
                or "Delegate" in self.spelling
            )
        else:
            return False

    def get_adopted_protocol_nodes(self):
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

    @property
    def token_string(self):
        return "".join(token.spelling for token in self.get_tokens())

    def get_struct_field_decls(self):
        if self.kind != CursorKind.STRUCT_DECL:
            return None
        return filter(lambda x: x.kind == CursorKind.FIELD_DECL, self.get_children())

    def get_function_specifiers(self):
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

    @property
    def first_child(self):
        child_holder = []

        def first_child_visitor(child, parent, holder):
            assert child != conf.lib.clang_getNullCursor()
            assert parent != child
            # Create reference to TU so it isn't GC'd before Cursor.
            child._tu = self._tu
            holder.append(child)
            return 0  # CXChildVisit_Break

        conf.lib.clang_visitChildren(
            self, callbacks["cursor_visit"](first_child_visitor), child_holder
        )

        return None if len(child_holder) == 0 else child_holder[0]

    def get_property_attributes(
        self,
    ) -> typing.Optional[typing.Set[typing.Union[str, typing.Tuple[str, str]]]]:
        if self.kind != CursorKind.OBJC_PROPERTY_DECL:
            return None

        attr_flags = conf.lib.clang_Cursor_getObjCPropertyAttributes(self, 0)

        attrs: typing.Set[typing.Union[str, typing.Tuple[str, str]]] = set()
        for flag, attr in _attr_flag_dict.items():
            if attr_flags & flag:
                attrs.add(attr)

        typestr = self.objc_type_encoding
        assert typestr is not None

        if "getter" in attrs or "setter" in attrs:
            for elem in typestr.split(b","):
                if elem.startswith(b"G"):
                    attrs.remove("getter")
                    attrs.add(("getter", elem[1:].decode()))
                elif elem.startswith(b"S"):
                    attrs.remove("setter")
                    attrs.add(("setter", elem[1:].decode()))

        return attrs

    @property
    def canonical_distint(self):
        if self == self.canonical:
            return None
        else:
            return self.canonical

    @property
    def referenced_distinct(self):
        if (not self.referenced) or self == self.referenced:
            return None
        else:
            return self.referenced

    @property
    def is_virtual(self):
        return conf.lib.clang_CXXMethod_isVirtual(self)

    @property
    def is_optional_for_protocol(self):
        return bool(conf.lib.clang_Cursor_isObjCOptional(self))

    @property
    def is_variadic(self):
        return bool(conf.lib.clang_Cursor_isVariadic(self))

    @property
    def type_valid(self):
        if self.type.kind == TypeKind.INVALID:
            return None
        else:
            return self.type

    @property
    def result_type_valid(self):
        if self.result_type.kind == TypeKind.INVALID:
            return None
        else:
            return self.result_type

    @property
    def result_type(self):
        # See note above;  Yes, we are *replacing* the
        # implementation of result_type from libclang
        if not hasattr(self, "_result_type"):
            self._result_type = conf.lib.clang_getCursorResultType(self)
        return self._result_type

    @property
    def platform_availability(self):
        always_deprecated = ctypes.c_int()
        deprecated_message = _CXString()
        always_unavailable = ctypes.c_int()
        unavailable_message = _CXString()
        availability_size = 10
        availability = (PlatformAvailability * availability_size)()

        r = conf.lib.clang_getCursorPlatformAvailability(
            self,
            ctypes.byref(always_deprecated),
            ctypes.byref(deprecated_message),
            ctypes.byref(always_unavailable),
            ctypes.byref(unavailable_message),
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


_attr_flag_dict = {
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


class StorageClass(enum.IntEnum):
    """
    Describes the storage class of a declaration
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    INVALID = 0
    NONE = 1
    EXTERN = 2
    STATIC = 3
    PRIVATEEXTERN = 4
    OPENCLWORKGROUPLOCAL = 5
    AUTO = 6
    REGISTER = 7


# Availability Kinds


class AvailabilityKind(enum.IntEnum):
    """
    Describes the availability of an entity.
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    AVAILABLE = 0
    DEPRECATED = 1
    NOT_AVAILABLE = 2
    NOT_ACCESSIBLE = 3


# C++ access specifiers


class AccessSpecifier(enum.IntEnum):
    """
    Describes the access of a C++ class member
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    INVALID = 0
    PUBLIC = 1
    PROTECTED = 2
    PRIVATE = 3
    NONE = 4


# Type Kinds


class TypeKind(enum.IntEnum):
    """
    Describes the kind of type.
    """

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    @property
    def spelling(self):
        """Retrieve the spelling of this TypeKind."""
        return conf.lib.clang_getTypeKindSpelling(self.value)

    def objc_type_for_arch(self, arch):
        assert arch is not None
        typekind_by_arch_map = _typekind_by_arch_map.get(arch, None)
        assert typekind_by_arch_map is not None
        typekind = typekind_by_arch_map.get(self)
        return typekind

    INVALID = 0
    UNEXPOSED = 1
    VOID = 2
    BOOL = 3
    CHAR_U = 4
    UCHAR = 5
    CHAR16 = 6
    CHAR32 = 7
    USHORT = 8
    UINT = 9
    ULONG = 10
    ULONGLONG = 11
    UINT128 = 12
    CHAR_S = 13
    SCHAR = 14
    WCHAR = 15
    SHORT = 16
    INT = 17
    LONG = 18
    LONGLONG = 19
    INT128 = 20
    FLOAT = 21
    DOUBLE = 22
    LONGDOUBLE = 23
    NULLPTR = 24
    OVERLOAD = 25
    DEPENDENT = 26
    OBJCID = 27
    OBJCCLASS = 28
    OBJCSEL = 29
    FLOAT128 = 30
    HALF = 31
    COMPLEX = 100
    POINTER = 101
    BLOCKPOINTER = 102
    LVALUEREFERENCE = 103
    RVALUEREFERENCE = 104
    RECORD = 105
    ENUM = 106
    TYPEDEF = 107
    OBJCINTERFACE = 108
    OBJCOBJECTPOINTER = 109
    FUNCTIONNOPROTO = 110
    FUNCTIONPROTO = 111
    CONSTANTARRAY = 112
    VECTOR = 113
    INCOMPLETEARRAY = 114
    VARIABLEARRAY = 115
    DEPENDENTSIZEDARRAY = 116
    MEMBERPOINTER = 117
    AUTO = 118
    ELABORATED = 119
    PIPE = 120
    OCLIMAGE1DRO = 121
    OCLIMAGE1DARRAYRO = 122
    OCLIMAGE1DBUFFERRO = 123
    OCLIMAGE2DRO = 124
    OCLIMAGE2DARRAYRO = 125
    OCLIMAGE2DDEPTHRO = 126
    OCLIMAGE2DARRAYDEPTHRO = 127
    OCLIMAGE2DMSAARO = 128
    OCLIMAGE2DARRAYMSAARO = 129
    OCLIMAGE2DMSAADEPTHRO = 130
    OCLIMAGE2DARRAYMSAADEPTHRO = 131
    OCLIMAGE3DRO = 132
    OCLIMAGE1DWO = 133
    OCLIMAGE1DARRAYWO = 134
    OCLIMAGE1DBUFFERWO = 135
    OCLIMAGE2DWO = 136
    OCLIMAGE2DARRAYWO = 137
    OCLIMAGE2DDEPTHWO = 138
    OCLIMAGE2DARRAYDEPTHWO = 139
    OCLIMAGE2DMSAAWO = 140
    OCLIMAGE2DARRAYMSAAWO = 141
    OCLIMAGE2DMSAADEPTHWO = 142
    OCLIMAGE2DARRAYMSAADEPTHWO = 143
    OCLIMAGE3DWO = 144
    OCLIMAGE1DRW = 145
    OCLIMAGE1DARRAYRW = 146
    OCLIMAGE1DBUFFERRW = 147
    OCLIMAGE2DRW = 148
    OCLIMAGE2DARRAYRW = 149
    OCLIMAGE2DDEPTHRW = 150
    OCLIMAGE2DARRAYDEPTHRW = 151
    OCLIMAGE2DMSAARW = 152
    OCLIMAGE2DARRAYMSAARW = 153
    OCLIMAGE2DMSAADEPTHRW = 154
    OCLIMAGE2DARRAYMSAADEPTHRW = 155
    OCLIMAGE3DRW = 156
    OCLSAMPLER = 157
    OCLEVENT = 158
    OCLQUEUE = 159
    OCLRESERVEID = 160
    OBJCOBJECT = 161
    OBJCTYPEPARAM = 162
    ATTRIBUTED = 163
    EXTVECTOR = 176
    ATOMIC = 177

    OCLIntelSubgroupAVCMcePayload = 164
    OCLIntelSubgroupAVCImePayload = 165
    OCLIntelSubgroupAVCRefPayload = 166
    OCLIntelSubgroupAVCSicPayload = 167
    OCLIntelSubgroupAVCMceResult = 168
    OCLIntelSubgroupAVCImeResult = 169
    OCLIntelSubgroupAVCRefResult = 170
    OCLIntelSubgroupAVCSicResult = 171
    OCLIntelSubgroupAVCImeResultSingleRefStreamout = 172
    OCLIntelSubgroupAVCImeResultDualRefStreamout = 173
    OCLIntelSubgroupAVCImeSingleRefStreamin = 174
    OCLIntelSubgroupAVCImeDualRefStreamin = 175


# noinspection PyProtectedMember
_typekind_to_objc_types_map_common = {
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
_typekind_to_objc_types_map_64 = {
    # CXType_ULong = 10,
    TypeKind.ULONG: objc._C_ULNG_LNG,
    # CXType_Long = 18,
    TypeKind.LONG: objc._C_LNG_LNG,
}

# noinspection PyProtectedMember
_typekind_to_objc_types_map_32 = {
    # CXType_ULong = 10,
    TypeKind.ULONG: objc._C_ULNG,
    # CXType_Long = 18,
    TypeKind.LONG: objc._C_LNG,
}

_typekind_by_arch_map = {
    "arm64": dict(
        itertools.chain(
            _typekind_to_objc_types_map_common.items(),
            _typekind_to_objc_types_map_64.items(),
        )
    ),
    "x86_64": dict(
        itertools.chain(
            _typekind_to_objc_types_map_common.items(),
            _typekind_to_objc_types_map_64.items(),
        )
    ),
    "ppc64": dict(
        itertools.chain(
            _typekind_to_objc_types_map_common.items(),
            _typekind_to_objc_types_map_64.items(),
        )
    ),
    "i386": dict(
        itertools.chain(
            _typekind_to_objc_types_map_common.items(),
            _typekind_to_objc_types_map_32.items(),
        )
    ),
}


class RefQualifierKind(enum.IntEnum):
    """Describes a specific ref-qualifier of a type."""

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    NONE = 0
    LVALUE = 1
    RVALUE = 2


class LinkageKind(enum.IntEnum):
    """Describes the kind of linkage of a cursor."""

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    INVALID = 0
    NO_LINKAGE = 1
    INTERNAL = 2
    UNIQUE_EXTERNAL = 3
    EXTERNAL = 4


class TLSKind(enum.IntEnum):
    """Describes the kind of thread-local storage (TLS) of a cursor."""

    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    NONE = 0
    DYNAMIC = 1
    STATIC = 2


class NullabilityKind(enum.IntEnum):
    def from_param(self):
        return self.value

    @classmethod
    def from_id(cls, value):
        return cls(value)

    NONNULL = 0
    NULLABLE = 1
    UNSPECIFIED = 2
    INVALID = 3


class Type(ctypes.Structure):
    """
    The type of an element in the abstract syntax tree.
    """

    _fields_ = [("_kind_id", ctypes.c_int), ("data", ctypes.c_void_p * 2)]

    def __hash__(self):
        return hash((self._kind_id, tuple(self.data)))

    @property
    def kind(self):
        """Return the kind of this type."""
        return TypeKind.from_id(self._kind_id)

    def argument_types(self):
        """Retrieve a container for the non-variadic arguments for this type.

        The returned object is iterable and indexable. Each item in the
        container is a Type instance.
        """

        class ArgumentsIterator(collections.abc.Sequence):
            def __init__(self, parent):
                self.parent = parent
                self.length = None

            def __len__(self):
                if self.length is None:
                    self.length = conf.lib.clang_getNumArgTypes(self.parent)

                return self.length

            def __getitem__(self, key):
                # libclang had a fix-me here of: Support slice objects.
                if not isinstance(key, int):
                    raise TypeError("Must supply a non-negative int.")

                if key < 0:
                    raise IndexError("Only non-negative indexes are accepted.")

                if key >= len(self):
                    raise IndexError(
                        "Index greater than container length: "
                        "%d > %d" % (key, len(self))
                    )

                result = conf.lib.clang_getArgType(self.parent, key)
                if result.kind == TypeKind.INVALID:
                    raise IndexError("Argument could not be retrieved.")

                return result

        return ArgumentsIterator(self)

    @property
    def element_type(self):
        """Retrieve the Type of elements within this Type.

        If accessed on a type that is not an array, complex, or vector type, an
        exception will be raised.
        """
        result = conf.lib.clang_getElementType(self)
        if result.kind == TypeKind.INVALID:
            raise Exception("Element type not available on this type.")

        return result

    @property
    def translation_unit(self):
        """The TranslationUnit to which this Type is associated."""
        # If this triggers an AttributeError, the instance was not properly
        # instantiated.
        return self._tu

    @staticmethod
    def from_result(res, fn, args):
        assert isinstance(res, Type)

        tu = None
        for arg in args:
            if hasattr(arg, "translation_unit"):
                tu = arg.translation_unit
                break

        res._tu = tu

        return res

    def get_num_template_arguments(self):
        return conf.lib.clang_Type_getNumTemplateArguments(self)

    def get_template_argument_type(self, num):
        return conf.lib.clang_Type_getTemplateArgumentAsType(self, num)

    def get_canonical(self):
        """
        Return the canonical type for a Type.

        Clang's type system explicitly models typedefs and all the
        ways a specific type can be represented.  The canonical type
        is the underlying type with all the "sugar" removed.  For
        example, if 'T' is a typedef for 'int', the canonical type for
        'T' would be 'int'.
        """
        return conf.lib.clang_getCanonicalType(self)

    def is_const_qualified(self):
        """Determine whether a Type has the "const" qualifier set.

        This does not look through typedefs that may have added "const"
        at a different level.
        """
        return conf.lib.clang_isConstQualifiedType(self)

    def is_volatile_qualified(self):
        """Determine whether a Type has the "volatile" qualifier set.

        This does not look through typedefs that may have added "volatile"
        at a different level.
        """
        return conf.lib.clang_isVolatileQualifiedType(self)

    def is_restrict_qualified(self):
        """Determine whether a Type has the "restrict" qualifier set.

        This does not look through typedefs that may have added "restrict" at
        a different level.
        """
        return conf.lib.clang_isRestrictQualifiedType(self)

    def get_address_space(self):
        return conf.lib.clang_getAddressSpace(self)

    def get_typedef_name(self):
        return conf.lib.clang_getTypedefName(self)

    def is_pod(self):
        """Determine whether this Type represents plain old data (POD)."""
        return conf.lib.clang_isPODType(self)

    def get_pointee(self):
        """
        For pointer types, returns the type of the pointee.
        """
        return conf.lib.clang_getPointeeType(self)

    def get_declaration(self):
        """
        Return the cursor for the declaration of the given type.
        """
        return conf.lib.clang_getTypeDeclaration(self)

    def get_result(self):
        """
        Retrieve the result type associated with a function type.
        """
        return conf.lib.clang_getResultType(self)

    def get_array_element_type(self):
        """
        Retrieve the type of the elements of the array type.
        """
        return conf.lib.clang_getArrayElementType(self)

    def get_array_size(self):
        """
        Retrieve the size of the constant array.
        """
        return conf.lib.clang_getArraySize(self)

    def get_class_type(self):
        """
        Retrieve the class type of the member pointer type.
        """
        return conf.lib.clang_Type_getClassType(self)

    def get_named_type(self):
        """
        Retrieve the type named by the qualified-id.
        """
        return conf.lib.clang_Type_getNamedType(self)

    def get_align(self) -> int:
        """
        Retrieve the alignment of the record.
        """
        return conf.lib.clang_Type_getAlignOf(self)

    def get_size(self) -> int:
        """
        Retrieve the size of the record.
        """
        return conf.lib.clang_Type_getSizeOf(self)

    def get_offset(self, fieldname: str) -> int:
        """
        Retrieve the offset of a field in the record.
        """
        return conf.lib.clang_Type_getOffsetOf(self, fieldname.encode())

    def get_ref_qualifier(self) -> RefQualifierKind:
        """
        Retrieve the ref-qualifier of the type.
        """
        return RefQualifierKind.from_id(conf.lib.clang_Type_getCXXRefQualifier(self))

    def get_fields(self):
        """Return an iterator for accessing the fields of this type."""

        def visitor(field, children):
            assert field != conf.lib.clang_getNullCursor()

            # Create reference to TU so it isn't GC'd before Cursor.
            field._tu = self._tu
            fields.append(field)
            return 1  # continue

        fields = []
        conf.lib.clang_Type_visitFields(
            self, callbacks["fields_visit"](visitor), fields
        )
        return iter(fields)

    def get_exception_specification_kind(self):
        """
        Return the kind of the exception specification; a value from
        the ExceptionSpecificationKind enumeration.
        """
        return ExceptionSpecificationKind.from_id(
            conf.lib.clang.getExceptionSpecificationType(self)
        )

    @property
    def spelling(self) -> str:
        """Retrieve the spelling of this Type."""
        return conf.lib.clang_getTypeSpelling(self)

    def __eq__(self, other: typing.Any) -> bool:
        if type(other) != type(self):
            return False

        return conf.lib.clang_equalTypes(self, other)

    def __ne__(self, other: typing.Any) -> bool:
        return not self.__eq__(other)

    @property
    def declaration(self) -> typing.Optional[Cursor]:
        decl = self.get_declaration()
        if decl.kind == CursorKind.NO_DECL_FOUND:
            return None
        else:
            return decl

    @property
    def quals(self) -> typing.Optional[typing.List[str]]:
        quals = []
        if self.is_const_qualified():
            quals.append("const")
        if self.is_volatile_qualified():
            quals.append("volatile")
        if self.is_restrict_qualified():
            quals.append("restrict")

        return quals if len(quals) > 0 else None

    def is_function_variadic(self):
        """Determine whether this function Type is a variadic function type."""
        # assert self.kind == TypeKind.FUNCTIONPROTO
        return conf.lib.clang_isFunctionTypeVariadic(self)

    @property
    def looks_like_function(self):
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

    @property
    def pointee(self):
        pointee = self.get_pointee()
        if pointee and pointee.kind != TypeKind.INVALID and pointee != self:
            return pointee
        return None

    @property
    def valid_type(self) -> typing.Optional["Type"]:
        if self.kind == TypeKind.INVALID:
            return None

        return self

    def ever_passes_test(self, pred):
        if pred(self):
            return True
        elif self.kind == TypeKind.TYPEDEF:
            td = self.declaration
            ult = None if td is None else td.underlying_typedef_type_valid
            if ult:
                return ult.ever_passes_test(pred)

        return False

    def ever_defines_to(self, type_name):
        return self.ever_passes_test(
            lambda x: (
                getattr(x, "spelling", "") == type_name
                or (x.declaration and x.declaration.spelling == type_name)
            )
        )

    @property
    def next_typedefed_type(self):
        if self.kind != TypeKind.TYPEDEF:
            return None
        td = self.declaration
        ult = None if td is None else td.underlying_typedef_type_valid
        return ult

    @property
    def element_count(self) -> typing.Optional[int]:
        """Retrieve the number of elements in this type.

        Replacing the method from the libclang python bindings because
        the stock one throws an exception,
        of type *Exception*. Sheesh.

        Returns an int.

        If the Type is not an array or vector, this returns None
        """
        result = conf.lib.clang_getNumElements(self)
        if result < 0:
            return None

        return result

    @property
    def nullability(self):
        if not hasattr(self, "_nullability"):
            self._nullability = conf.lib.clang_Type_getNullability(self)

        return NullabilityKind.from_id(self._nullability)

    @property
    def modified_type(self):
        return conf.lib.clang_Type_getModifiedType(self)


# CIndex Objects

# CIndex objects (derived from ClangObject) are essentially lightweight
# wrappers attached to some underlying object, which is exposed via CIndex as
# a void*.


class ClangObject(object):
    """
    A helper for Clang objects. This class helps act as an intermediary for
    the ctypes library and the Clang CIndex library.
    """

    def __init__(self, obj):
        assert isinstance(obj, c_object_p) and obj
        self.obj = self._as_parameter_ = obj

    def from_param(self):
        return self._as_parameter_


class _CXUnsavedFile(ctypes.Structure):
    """Helper for passing unsaved file arguments."""

    _fields_ = [
        ("name", ctypes.c_char_p),
        ("contents", ctypes.c_char_p),
        ("length", ctypes.c_ulong),
    ]


# Functions calls through the python interface are rather slow. Fortunately,
# for most symboles, we do not need to perform a function call. Their spelling
# never changes and is consequently provided by this spelling cache.
SpellingCache = {
    # 0: CompletionChunk.Kind("Optional"),
    # 1: CompletionChunk.Kind("TypedText"),
    # 2: CompletionChunk.Kind("Text"),
    # 3: CompletionChunk.Kind("Placeholder"),
    # 4: CompletionChunk.Kind("Informative"),
    # 5 : CompletionChunk.Kind("CurrentParameter"),
    6: "(",  # CompletionChunk.Kind("LeftParen"),
    7: ")",  # CompletionChunk.Kind("RightParen"),
    8: "[",  # CompletionChunk.Kind("LeftBracket"),
    9: "]",  # CompletionChunk.Kind("RightBracket"),
    10: "{",  # CompletionChunk.Kind("LeftBrace"),
    11: "}",  # CompletionChunk.Kind("RightBrace"),
    12: "<",  # CompletionChunk.Kind("LeftAngle"),
    13: ">",  # CompletionChunk.Kind("RightAngle"),
    14: ", ",  # CompletionChunk.Kind("Comma"),
    # 15: CompletionChunk.Kind("ResultType"),
    16: ":",  # CompletionChunk.Kind("Colon"),
    17: ";",  # CompletionChunk.Kind("SemiColon"),
    18: "=",  # CompletionChunk.Kind("Equal"),
    19: " ",  # CompletionChunk.Kind("HorizontalSpace"),
    # 20: CompletionChunk.Kind("VerticalSpace")
}


class CompletionChunk(object):
    class Kind(object):
        def __init__(self, name):
            self.name = name

        def __str__(self):
            return self.name

        def __repr__(self):
            return "<ChunkKind: %s>" % self

    def __init__(self, completionString, key):
        self.cs = completionString
        self.key = key
        self.__kindNumberCache = -1

    def __repr__(self):
        return "{'" + self.spelling + "', " + str(self.kind) + "}"

    @CachedProperty
    def spelling(self):
        if self.__kindNumber in SpellingCache:
            return SpellingCache[self.__kindNumber]
        return conf.lib.clang_getCompletionChunkText(self.cs, self.key)

    # We do not use @CachedProperty here, as the manual implementation is
    # apparently still significantly faster. Please profile carefully if you
    # would like to add CachedProperty back.
    @property
    def __kindNumber(self):
        if self.__kindNumberCache == -1:
            self.__kindNumberCache = conf.lib.clang_getCompletionChunkKind(
                self.cs, self.key
            )
        return self.__kindNumberCache

    @CachedProperty
    def kind(self):
        return completionChunkKindMap[self.__kindNumber]

    @CachedProperty
    def string(self):
        res = conf.lib.clang_getCompletionChunkCompletionString(self.cs, self.key)

        if res:
            return CompletionString(res)
        else:
            None

    def isKindOptional(self):
        return self.__kindNumber == 0

    def isKindTypedText(self):
        return self.__kindNumber == 1

    def isKindPlaceHolder(self):
        return self.__kindNumber == 3

    def isKindInformative(self):
        return self.__kindNumber == 4

    def isKindResultType(self):
        return self.__kindNumber == 15


completionChunkKindMap = {
    0: CompletionChunk.Kind("Optional"),
    1: CompletionChunk.Kind("TypedText"),
    2: CompletionChunk.Kind("Text"),
    3: CompletionChunk.Kind("Placeholder"),
    4: CompletionChunk.Kind("Informative"),
    5: CompletionChunk.Kind("CurrentParameter"),
    6: CompletionChunk.Kind("LeftParen"),
    7: CompletionChunk.Kind("RightParen"),
    8: CompletionChunk.Kind("LeftBracket"),
    9: CompletionChunk.Kind("RightBracket"),
    10: CompletionChunk.Kind("LeftBrace"),
    11: CompletionChunk.Kind("RightBrace"),
    12: CompletionChunk.Kind("LeftAngle"),
    13: CompletionChunk.Kind("RightAngle"),
    14: CompletionChunk.Kind("Comma"),
    15: CompletionChunk.Kind("ResultType"),
    16: CompletionChunk.Kind("Colon"),
    17: CompletionChunk.Kind("SemiColon"),
    18: CompletionChunk.Kind("Equal"),
    19: CompletionChunk.Kind("HorizontalSpace"),
    20: CompletionChunk.Kind("VerticalSpace"),
}


class CompletionString(ClangObject):
    class Availability(object):
        def __init__(self, name):
            self.name = name

        def __str__(self):
            return self.name

        def __repr__(self):
            return "<Availability: %s>" % self

    def __len__(self):
        return self.num_chunks

    @CachedProperty
    def num_chunks(self):
        return conf.lib.clang_getNumCompletionChunks(self.obj)

    def __getitem__(self, key):
        if self.num_chunks <= key:
            raise IndexError
        return CompletionChunk(self.obj, key)

    @property
    def priority(self):
        return conf.lib.clang_getCompletionPriority(self.obj)

    @property
    def availability(self):
        res = conf.lib.clang_getCompletionAvailability(self.obj)
        return availabilityKinds[res]

    @property
    def briefComment(self):
        if conf.function_exists("clang_getCompletionBriefComment"):
            return conf.lib.clang_getCompletionBriefComment(self.obj)
        return _CXString()

    def __repr__(self):
        return (
            " | ".join([str(a) for a in self])
            + " || Priority: "
            + str(self.priority)
            + " || Availability: "
            + str(self.availability)
            + " || Brief comment: "
            + str(self.briefComment)
        )


availabilityKinds = {
    0: CompletionChunk.Kind("Available"),
    1: CompletionChunk.Kind("Deprecated"),
    2: CompletionChunk.Kind("NotAvailable"),
    3: CompletionChunk.Kind("NotAccessible"),
}


class CodeCompletionResult(ctypes.Structure):
    _fields_ = [("cursorKind", ctypes.c_int), ("completionString", c_object_p)]

    def __repr__(self):
        return str(CompletionString(self.completionString))

    @property
    def kind(self):
        return CursorKind.from_id(self.cursorKind)

    @property
    def string(self):
        return CompletionString(self.completionString)


class CCRStructure(ctypes.Structure):
    _fields_ = [
        ("results", ctypes.POINTER(CodeCompletionResult)),
        ("numResults", ctypes.c_int),
    ]

    def __len__(self):
        return self.numResults

    def __getitem__(self, key):
        if len(self) <= key:
            raise IndexError

        return self.results[key]


class CodeCompletionResults(ClangObject):
    def __init__(self, ptr):
        assert isinstance(ptr, ctypes.POINTER(CCRStructure)) and ptr
        self.ptr = self._as_parameter_ = ptr

    def from_param(self):
        return self._as_parameter_

    def __del__(self):
        conf.lib.clang_disposeCodeCompleteResults(self)

    @property
    def results(self):
        return self.ptr.contents

    @property
    def diagnostics(self):
        class DiagnosticsItr(object):
            def __init__(self, ccr):
                self.ccr = ccr

            def __len__(self):
                return int(conf.lib.clang_codeCompleteGetNumDiagnostics(self.ccr))

            def __getitem__(self, key):
                return conf.lib.clang_codeCompleteGetDiagnostic(self.ccr, key)

        return DiagnosticsItr(self)


class Index(ClangObject):
    """
    The Index type provides the primary interface to the Clang CIndex library,
    primarily by providing an interface for reading and parsing translation
    units.
    """

    @staticmethod
    def create(excludeDecls=False):
        """
        Create a new Index.
        Parameters:
        excludeDecls -- Exclude local declarations from translation units.
        """
        return Index(conf.lib.clang_createIndex(excludeDecls, 0))

    def __del__(self):
        conf.lib.clang_disposeIndex(self)

    def read(self, path):
        """Load a TranslationUnit from the given AST file."""
        return TranslationUnit.from_ast_file(path, self)

    def parse(self, path, args=None, unsaved_files=None, options=0):
        """Load the translation unit from the given source code file by running
        clang and generating the AST before loading. Additional command line
        parameters can be passed to clang via the args parameter.

        In-memory contents for files can be provided by passing a list of pairs
        to as unsaved_files, the first item should be the filenames to be mapped
        and the second should be the contents to be substituted for the
        file. The contents may be passed as strings or file objects.

        If an error was encountered during parsing, a TranslationUnitLoadError
        will be raised.
        """
        return TranslationUnit.from_source(path, args, unsaved_files, options, self)


class TranslationUnit(ClangObject):
    """Represents a source code translation unit.

    This is one of the main types in the API. Any time you wish to interact
    with Clang's representation of a source file, you typically start with a
    translation unit.
    """

    # Default parsing mode.
    PARSE_NONE = 0

    # Instruct the parser to create a detailed processing record containing
    # metadata not normally retained.
    PARSE_DETAILED_PROCESSING_RECORD = 1

    # Indicates that the translation unit is incomplete. This is typically used
    # when parsing headers.
    PARSE_INCOMPLETE = 2

    # Instruct the parser to create a pre-compiled preamble for the translation
    # unit. This caches the preamble (included files at top of source file).
    # This is useful if the translation unit will be reparsed and you don't
    # want to incur the overhead of reparsing the preamble.
    PARSE_PRECOMPILED_PREAMBLE = 4

    # Cache code completion information on parse. This adds time to parsing but
    # speeds up code completion.
    PARSE_CACHE_COMPLETION_RESULTS = 8

    # Flags with values 16 and 32 are deprecated and intentionally omitted.

    # Do not parse function bodies. This is useful if you only care about
    # searching for declarations/definitions.
    PARSE_SKIP_FUNCTION_BODIES = 64

    # Used to indicate that brief documentation comments should be included
    # into the set of code completions returned from this translation unit.
    PARSE_INCLUDE_BRIEF_COMMENTS_IN_CODE_COMPLETION = 128

    INCLUDE_ATTRIBUTED_TYPES = 0x1000
    VISIT_IMPLICIT_ATTRIBUTES = 0x2000

    @classmethod
    def from_source(
        cls, filename, args=None, unsaved_files=None, options=0, index=None
    ):
        """Create a TranslationUnit by parsing source.

        This is capable of processing source code both from files on the
        filesystem as well as in-memory contents.

        Command-line arguments that would be passed to clang are specified as
        a list via args. These can be used to specify include paths, warnings,
        etc. e.g. ["-Wall", "-I/path/to/include"].

        In-memory file content can be provided via unsaved_files. This is an
        iterable of 2-tuples. The first element is the filename (str or
        PathLike). The second element defines the content. Content can be
        provided as str source code or as file objects (anything with a read()
        method). If a file object is being used, content will be read until EOF
        and the read cursor will not be reset to its original position.

        options is a bitwise or of TranslationUnit.PARSE_XXX flags which will
        control parsing behavior.

        index is an Index instance to utilize. If not provided, a new Index
        will be created for this TranslationUnit.

        To parse source from the filesystem, the filename of the file to parse
        is specified by the filename argument. Or, filename could be None and
        the args list would contain the filename(s) to parse.

        To parse source from an in-memory buffer, set filename to the virtual
        filename you wish to associate with this source (e.g. "test.c"). The
        contents of that file are then provided in unsaved_files.

        If an error occurs, a TranslationUnitLoadError is raised.

        Please note that a TranslationUnit with parser errors may be returned.
        It is the caller's responsibility to check tu.diagnostics for errors.

        Also note that Clang infers the source language from the extension of
        the input filename. If you pass in source code containing a C++ class
        declaration with the filename "test.c" parsing will fail.
        """
        if args is None:
            args = []

        if unsaved_files is None:
            unsaved_files = []

        if index is None:
            index = Index.create()

        args_array = None
        if len(args) > 0:
            args_array = (ctypes.c_char_p * len(args))(*[b(x) for x in args])

        unsaved_array = None
        if len(unsaved_files) > 0:
            unsaved_array = (_CXUnsavedFile * len(unsaved_files))()
            for i, (name, contents) in enumerate(unsaved_files):
                if hasattr(contents, "read"):
                    contents = contents.read()
                contents = b(contents)
                unsaved_array[i].name = b(os.fspath(name))
                unsaved_array[i].contents = contents
                unsaved_array[i].length = len(contents)

        ptr = conf.lib.clang_parseTranslationUnit(
            index,
            os.fspath(filename).encode() if filename is not None else None,
            args_array,
            len(args),
            unsaved_array,
            len(unsaved_files),
            options,
        )

        if not ptr:
            raise TranslationUnitLoadError("Error parsing translation unit.")

        return cls(ptr, index=index)

    @classmethod
    def from_ast_file(cls, filename, index=None):
        """Create a TranslationUnit instance from a saved AST file.

        A previously-saved AST file (provided with -emit-ast or
        TranslationUnit.save()) is loaded from the filename specified.

        If the file cannot be loaded, a TranslationUnitLoadError will be
        raised.

        index is optional and is the Index instance to use. If not provided,
        a default Index will be created.

        filename can be str or PathLike.
        """
        if index is None:
            index = Index.create()

        ptr = conf.lib.clang_createTranslationUnit(index, os.fspath(filename).encode())
        if not ptr:
            raise TranslationUnitLoadError(filename)

        return cls(ptr=ptr, index=index)

    def __init__(self, ptr, index):
        """Create a TranslationUnit instance.

        TranslationUnits should be created using one of the from_* @classmethod
        functions above. __init__ is only called internally.
        """
        assert isinstance(index, Index)
        self.index = index
        ClangObject.__init__(self, ptr)

    def __del__(self):
        conf.lib.clang_disposeTranslationUnit(self)

    @property
    def cursor(self):
        """Retrieve the cursor that represents the given translation unit."""
        return conf.lib.clang_getTranslationUnitCursor(self)

    @property
    def spelling(self):
        """Get the original translation unit source file name."""
        return conf.lib.clang_getTranslationUnitSpelling(self)

    def get_includes(self):
        """
        Return an iterable sequence of FileInclusion objects that describe the
        sequence of inclusions in a translation unit. The first object in
        this sequence is always the input file. Note that this method will not
        recursively iterate over header files included through precompiled
        headers.
        """

        def visitor(fobj, lptr, depth, includes):
            if depth > 0:
                loc = lptr.contents
                includes.append(FileInclusion(loc.file, File(fobj), loc, depth))

        # Automatically adapt CIndex/ctype pointers to python objects
        includes = []
        conf.lib.clang_getInclusions(
            self, callbacks["translation_unit_includes"](visitor), includes
        )

        return iter(includes)

    def get_file(self, filename):
        """Obtain a File from this translation unit."""

        return File.from_name(self, filename)

    def get_location(self, filename, position):
        """Obtain a SourceLocation for a file in this translation unit.

        The position can be specified by passing:

          - Integer file offset. Initial file offset is 0.
          - 2-tuple of (line number, column number). Initial file position is
            (0, 0)
        """
        f = self.get_file(filename)

        if isinstance(position, int):
            return SourceLocation.from_offset(self, f, position)

        return SourceLocation.from_position(self, f, position[0], position[1])

    def get_extent(self, filename, locations):
        """Obtain a SourceRange from this translation unit.

        The bounds of the SourceRange must ultimately be defined by a start and
        end SourceLocation. For the locations argument, you can pass:

          - 2 SourceLocation instances in a 2-tuple or list.
          - 2 int file offsets via a 2-tuple or list.
          - 2 2-tuple or lists of (line, column) pairs in a 2-tuple or list.

        e.g.

        get_extent('foo.c', (5, 10))
        get_extent('foo.c', ((1, 1), (1, 15)))
        """
        f = self.get_file(filename)

        if len(locations) < 2:
            raise Exception("Must pass object with at least 2 elements")

        start_location, end_location = locations

        if hasattr(start_location, "__len__"):
            start_location = SourceLocation.from_position(
                self, f, start_location[0], start_location[1]
            )
        elif isinstance(start_location, int):
            start_location = SourceLocation.from_offset(self, f, start_location)

        if hasattr(end_location, "__len__"):
            end_location = SourceLocation.from_position(
                self, f, end_location[0], end_location[1]
            )
        elif isinstance(end_location, int):
            end_location = SourceLocation.from_offset(self, f, end_location)

        assert isinstance(start_location, SourceLocation)
        assert isinstance(end_location, SourceLocation)

        return SourceRange.from_locations(start_location, end_location)

    @property
    def diagnostics(self):
        """
        Return an iterable (and indexable) object containing the diagnostics.
        """

        class DiagIterator(object):
            def __init__(self, tu):
                self.tu = tu

            def __len__(self):
                return int(conf.lib.clang_getNumDiagnostics(self.tu))

            def __getitem__(self, key):
                diag = conf.lib.clang_getDiagnostic(self.tu, key)
                if not diag:
                    raise IndexError
                return Diagnostic(diag)

        return DiagIterator(self)

    def reparse(self, unsaved_files=None, options=0):
        """
        Reparse an already parsed translation unit.

        In-memory contents for files can be provided by passing a list of pairs
        as unsaved_files, the first items should be the filenames to be mapped
        and the second should be the contents to be substituted for the
        file. The contents may be passed as strings or file objects.
        """
        if unsaved_files is None:
            unsaved_files = []

        unsaved_files_array = 0
        if len(unsaved_files):
            unsaved_files_array = (_CXUnsavedFile * len(unsaved_files))()
            for i, (name, contents) in enumerate(unsaved_files):
                if hasattr(contents, "read"):
                    contents = contents.read()
                contents = b(contents)
                unsaved_files_array[i].name = b(os.fspath(name))
                unsaved_files_array[i].contents = contents
                unsaved_files_array[i].length = len(contents)
        conf.lib.clang_reparseTranslationUnit(
            self, len(unsaved_files), unsaved_files_array, options
        )

    def save(self, filename):
        """Saves the TranslationUnit to a file.

        This is equivalent to passing -emit-ast to the clang frontend. The
        saved file can be loaded back into a TranslationUnit. Or, if it
        corresponds to a header, it can be used as a pre-compiled header file.

        If an error occurs while saving, a TranslationUnitSaveError is raised.
        If the error was TranslationUnitSaveError.ERROR_INVALID_TU, this means
        the constructed TranslationUnit was not valid at time of save. In this
        case, the reason(s) why should be available via
        TranslationUnit.diagnostics().

        filename -- The path to save the translation unit to (str or PathLike).
        """
        options = conf.lib.clang_defaultSaveOptions(self)
        result = int(
            conf.lib.clang_saveTranslationUnit(
                self, os.fspath(filename).encode(), options
            )
        )
        if result != 0:
            raise TranslationUnitSaveError(result, "Error saving TranslationUnit.")

    def codeComplete(
        self,
        path,
        line,
        column,
        unsaved_files=None,
        include_macros=False,
        include_code_patterns=False,
        include_brief_comments=False,
    ):
        """
        Code complete in this translation unit.

        In-memory contents for files can be provided by passing a list of pairs
        as unsaved_files, the first items should be the filenames to be mapped
        and the second should be the contents to be substituted for the
        file. The contents may be passed as strings or file objects.
        """
        options = 0

        if include_macros:
            options += 1

        if include_code_patterns:
            options += 2

        if include_brief_comments:
            options += 4

        if unsaved_files is None:
            unsaved_files = []

        unsaved_files_array = 0
        if len(unsaved_files):
            unsaved_files_array = (_CXUnsavedFile * len(unsaved_files))()
            for i, (name, contents) in enumerate(unsaved_files):
                if hasattr(contents, "read"):
                    contents = contents.read()
                contents = b(contents)
                unsaved_files_array[i].name = b(os.fspath(name))
                unsaved_files_array[i].contents = contents
                unsaved_files_array[i].length = len(contents)
        ptr = conf.lib.clang_codeCompleteAt(
            self,
            os.fspath(path).encode(),
            line,
            column,
            unsaved_files_array,
            len(unsaved_files),
            options,
        )
        if ptr:
            return CodeCompletionResults(ptr)
        return None

    def get_tokens(self, locations=None, extent=None):
        """Obtain tokens in this translation unit.

        This is a generator for Token instances. The caller specifies a range
        of source code to obtain tokens for. The range can be specified as a
        2-tuple of SourceLocation or as a SourceRange. If both are defined,
        behavior is undefined.
        """
        if locations is not None:
            extent = SourceRange(start=locations[0], end=locations[1])

        return TokenGroup.get_tokens(self, extent)


class File(ClangObject):
    """
    The File class represents a particular source file that is part of a
    translation unit.
    """

    @staticmethod
    def from_name(
        translation_unit: "TranslationUnit",
        file_name: typing.Union[str, os.PathLike[str]],
    ) -> "File":
        """Retrieve a file handle within the given translation unit."""
        return File(
            conf.lib.clang_getFile(translation_unit, os.fspath(file_name).encode())
        )

    @property
    def name(self) -> str:
        """Return the complete file and path name of the file."""
        return conf.lib.clang_getFileName(self)

    @property
    def time(self) -> float:
        """Return the last modification time of the file."""
        return conf.lib.clang_getFileTime(self)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return "<File: %s>" % (self.name)

    @staticmethod
    def from_result(res, fn, args):
        assert isinstance(res, c_object_p)
        res = File(res)

        # Copy a reference to the TranslationUnit to prevent premature GC.
        res._tu = args[0]._tu
        return res


class FileInclusion(object):
    """
    The FileInclusion class represents the inclusion of one source file by
    another via a '#include' directive or as the input file for the translation
    unit. This class provides information about the included file, the including
    file, the location of the '#include' directive and the depth of the included
    file in the stack. Note that the input file has depth 0.
    """

    def __init__(self, src, tgt, loc, depth):
        self.source = src
        self.include = tgt
        self.location = loc
        self.depth = depth

    @property
    def is_input_file(self):
        """True if the included file is the input file."""
        return self.depth == 0


class CompilationDatabaseError(Exception):
    """Represents an error that occurred when working with a CompilationDatabase

    Each error is associated to an enumerated value, accessible under
    e.cdb_error. Consumers can compare the value with one of the ERROR_
    constants in this class.
    """

    # An unknown error occurred
    ERROR_UNKNOWN = 0

    # The database could not be loaded
    ERROR_CANNOTLOADDATABASE = 1

    def __init__(self, enumeration, message):
        assert isinstance(enumeration, int)

        if enumeration > 1:
            raise Exception(
                "Encountered undefined CompilationDatabase error "
                "constant: %d. Please file a bug to have this "
                "value supported." % enumeration
            )

        self.cdb_error = enumeration
        Exception.__init__(self, "Error %d: %s" % (enumeration, message))


class CompileCommand(object):
    """Represents the compile command used to build a file"""

    def __init__(self, cmd, ccmds):
        self.cmd = cmd
        # Keep a reference to the originating CompileCommands
        # to prevent garbage collection
        self.ccmds = ccmds

    @property
    def directory(self):
        """Get the working directory for this CompileCommand"""
        return conf.lib.clang_CompileCommand_getDirectory(self.cmd)

    @property
    def filename(self):
        """Get the working filename for this CompileCommand"""
        return conf.lib.clang_CompileCommand_getFilename(self.cmd)

    @property
    def arguments(self):
        """
        Get an iterable object providing each argument in the
        command line for the compiler invocation as a _CXString.

        Invariant : the first argument is the compiler executable
        """
        length = conf.lib.clang_CompileCommand_getNumArgs(self.cmd)
        for i in range(length):
            yield conf.lib.clang_CompileCommand_getArg(self.cmd, i)


class CompileCommands(object):
    """
    CompileCommands is an iterable object containing all CompileCommand
    that can be used for building a specific file.
    """

    def __init__(self, ccmds):
        self.ccmds = ccmds

    def __del__(self):
        conf.lib.clang_CompileCommands_dispose(self.ccmds)

    def __len__(self):
        return int(conf.lib.clang_CompileCommands_getSize(self.ccmds))

    def __getitem__(self, i):
        cc = conf.lib.clang_CompileCommands_getCommand(self.ccmds, i)
        if not cc:
            raise IndexError
        return CompileCommand(cc, self)

    @staticmethod
    def from_result(res, fn, args):
        if not res:
            return None
        return CompileCommands(res)


class CompilationDatabase(ClangObject):
    """
    The CompilationDatabase is a wrapper class around
    clang::tooling::CompilationDatabase

    It enables querying how a specific source file can be built.
    """

    def __del__(self):
        conf.lib.clang_CompilationDatabase_dispose(self)

    @staticmethod
    def from_result(res, fn, args):
        if not res:
            raise CompilationDatabaseError(0, "CompilationDatabase loading failed")
        return CompilationDatabase(res)

    @staticmethod
    def fromDirectory(buildDir):
        """Builds a CompilationDatabase from the database found in buildDir"""
        errorCode = ctypes.c_uint()
        try:
            cdb = conf.lib.clang_CompilationDatabase_fromDirectory(
                os.fspath(buildDir).encode(), ctypes.byref(errorCode)
            )
        except CompilationDatabaseError:
            raise CompilationDatabaseError(
                int(errorCode.value), "CompilationDatabase loading failed"
            )
        return cdb

    def getCompileCommands(self, filename):
        """
        Get an iterable object providing all the CompileCommands available to
        build filename. Returns None if filename is not found in the database.
        """
        return conf.lib.clang_CompilationDatabase_getCompileCommands(
            self, os.fspath(filename).encode()
        )

    def getAllCompileCommands(self):
        """
        Get an iterable object providing all the CompileCommands available from
        the database.
        """
        return conf.lib.clang_CompilationDatabase_getAllCompileCommands(self)


class Token(ctypes.Structure):
    """Represents a single token from the preprocessor.

    Tokens are effectively segments of source code. Source code is first parsed
    into tokens before being converted into the AST and Cursors.

    Tokens are obtained from parsed TranslationUnit instances. You currently
    can't create tokens manually.
    """

    _fields_ = [("int_data", ctypes.c_uint * 4), ("ptr_data", ctypes.c_void_p)]

    @property
    def spelling(self) -> str:
        """The spelling of this token.

        This is the textual representation of the token in source.
        """
        return conf.lib.clang_getTokenSpelling(self._tu, self)

    @property
    def kind(self) -> TokenKind:
        """Obtain the TokenKind of the current token."""
        return TokenKind.from_value(conf.lib.clang_getTokenKind(self))

    @property
    def location(self) -> SourceLocation:
        """The SourceLocation this Token occurs at."""
        return conf.lib.clang_getTokenLocation(self._tu, self)

    @property
    def extent(self) -> SourceRange:
        """The SourceRange this Token occupies."""
        return conf.lib.clang_getTokenExtent(self._tu, self)

    @property
    def cursor(self) -> Cursor:
        """The Cursor this Token corresponds to."""
        cursor = Cursor()
        cursor._tu = self._tu

        conf.lib.clang_annotateTokens(
            self._tu, ctypes.byref(self), 1, ctypes.byref(cursor)
        )

        return cursor


# Now comes the plumbing to hook up the C library.

# Register callback types in common container.
callbacks["translation_unit_includes"] = ctypes.CFUNCTYPE(
    None, c_object_p, ctypes.POINTER(SourceLocation), ctypes.c_uint, ctypes.py_object
)
callbacks["cursor_visit"] = ctypes.CFUNCTYPE(
    ctypes.c_int, Cursor, Cursor, ctypes.py_object
)
callbacks["fields_visit"] = ctypes.CFUNCTYPE(ctypes.c_int, Cursor, ctypes.py_object)

# Functions strictly alphabetical order.
functionList = [
    (
        "clang_annotateTokens",
        [TranslationUnit, ctypes.POINTER(Token), ctypes.c_uint, ctypes.POINTER(Cursor)],
    ),
    ("clang_CompilationDatabase_dispose", [c_object_p]),
    (
        "clang_CompilationDatabase_fromDirectory",
        [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)],
        c_object_p,
        CompilationDatabase.from_result,
    ),
    (
        "clang_CompilationDatabase_getAllCompileCommands",
        [c_object_p],
        c_object_p,
        CompileCommands.from_result,
    ),
    (
        "clang_CompilationDatabase_getCompileCommands",
        [c_object_p, ctypes.c_char_p],
        c_object_p,
        CompileCommands.from_result,
    ),
    ("clang_CompileCommands_dispose", [c_object_p]),
    ("clang_CompileCommands_getCommand", [c_object_p, ctypes.c_uint], c_object_p),
    ("clang_CompileCommands_getSize", [c_object_p], ctypes.c_uint),
    (
        "clang_CompileCommand_getArg",
        [c_object_p, ctypes.c_uint],
        _CXString,
        _CXString.from_result,
    ),
    (
        "clang_CompileCommand_getDirectory",
        [c_object_p],
        _CXString,
        _CXString.from_result,
    ),
    (
        "clang_CompileCommand_getFilename",
        [c_object_p],
        _CXString,
        _CXString.from_result,
    ),
    ("clang_CompileCommand_getNumArgs", [c_object_p], ctypes.c_uint),
    (
        "clang_codeCompleteAt",
        [
            TranslationUnit,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ],
        ctypes.POINTER(CCRStructure),
    ),
    (
        "clang_codeCompleteGetDiagnostic",
        [CodeCompletionResults, ctypes.c_int],
        Diagnostic,
    ),
    ("clang_codeCompleteGetNumDiagnostics", [CodeCompletionResults], ctypes.c_int),
    ("clang_createIndex", [ctypes.c_int, ctypes.c_int], c_object_p),
    ("clang_createTranslationUnit", [Index, ctypes.c_char_p], c_object_p),
    ("clang_CXXConstructor_isConvertingConstructor", [Cursor], bool),
    ("clang_CXXConstructor_isCopyConstructor", [Cursor], bool),
    ("clang_CXXConstructor_isDefaultConstructor", [Cursor], bool),
    ("clang_CXXConstructor_isMoveConstructor", [Cursor], bool),
    ("clang_CXXField_isMutable", [Cursor], bool),
    ("clang_CXXMethod_isConst", [Cursor], bool),
    ("clang_CXXMethod_isDefaulted", [Cursor], bool),
    ("clang_CXXMethod_isPureVirtual", [Cursor], bool),
    ("clang_CXXMethod_isStatic", [Cursor], bool),
    ("clang_CXXMethod_isVirtual", [Cursor], bool),
    ("clang_CXXRecord_isAbstract", [Cursor], bool),
    ("clang_EnumDecl_isScoped", [Cursor], bool),
    ("clang_defaultDiagnosticDisplayOptions", [], ctypes.c_uint),
    ("clang_defaultSaveOptions", [TranslationUnit], ctypes.c_uint),
    ("clang_disposeCodeCompleteResults", [CodeCompletionResults]),
    # ("clang_disposeCXTUResourceUsage",
    #  [CXTUResourceUsage]),
    ("clang_disposeDiagnostic", [Diagnostic]),
    ("clang_disposeIndex", [Index]),
    ("clang_disposeString", [_CXString]),
    ("clang_disposeTokens", [TranslationUnit, ctypes.POINTER(Token), ctypes.c_uint]),
    ("clang_disposeTranslationUnit", [TranslationUnit]),
    ("clang_equalCursors", [Cursor, Cursor], bool),
    ("clang_equalLocations", [SourceLocation, SourceLocation], bool),
    ("clang_equalRanges", [SourceRange, SourceRange], bool),
    ("clang_equalTypes", [Type, Type], bool),
    (
        "clang_formatDiagnostic",
        [Diagnostic, ctypes.c_uint],
        _CXString,
        _CXString.from_result,
    ),
    ("clang_getArgType", [Type, ctypes.c_uint], Type, Type.from_result),
    ("clang_getArrayElementType", [Type], Type, Type.from_result),
    ("clang_getArraySize", [Type], ctypes.c_longlong),
    ("clang_getFieldDeclBitWidth", [Cursor], ctypes.c_int),
    ("clang_getCanonicalCursor", [Cursor], Cursor, Cursor.from_cursor_result),
    ("clang_getCanonicalType", [Type], Type, Type.from_result),
    ("clang_getChildDiagnostics", [Diagnostic], c_object_p),
    ("clang_getCompletionAvailability", [ctypes.c_void_p], ctypes.c_int),
    (
        "clang_getCompletionBriefComment",
        [ctypes.c_void_p],
        _CXString,
        _CXString.from_result,
    ),
    (
        "clang_getCompletionChunkCompletionString",
        [ctypes.c_void_p, ctypes.c_int],
        c_object_p,
    ),
    ("clang_getCompletionChunkKind", [ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
    (
        "clang_getCompletionChunkText",
        [ctypes.c_void_p, ctypes.c_int],
        _CXString,
        _CXString.from_result,
    ),
    ("clang_getCompletionPriority", [ctypes.c_void_p], ctypes.c_int),
    ("clang_getCString", [_CXString], ctypes.c_char_p, c_char_p_to_string),
    ("clang_getCursor", [TranslationUnit, SourceLocation], Cursor),
    ("clang_getCursorAvailability", [Cursor], ctypes.c_int),
    ("clang_getCursorDefinition", [Cursor], Cursor, Cursor.from_result),
    ("clang_getCursorDisplayName", [Cursor], _CXString, _CXString.from_result),
    ("clang_getCursorExtent", [Cursor], SourceRange),
    ("clang_getCursorLexicalParent", [Cursor], Cursor, Cursor.from_cursor_result),
    ("clang_getCursorLocation", [Cursor], SourceLocation),
    ("clang_getCursorReferenced", [Cursor], Cursor, Cursor.from_result),
    (
        "clang_getCursorReferenceNameRange",
        [Cursor, ctypes.c_uint, ctypes.c_uint],
        SourceRange,
    ),
    ("clang_getCursorResultType", [Cursor], Type, Type.from_result),
    ("clang_getCursorSemanticParent", [Cursor], Cursor, Cursor.from_cursor_result),
    ("clang_getCursorSpelling", [Cursor], _CXString, _CXString.from_result),
    ("clang_getCursorType", [Cursor], Type, Type.from_result),
    ("clang_getCursorUSR", [Cursor], _CXString, _CXString.from_result),
    ("clang_Cursor_getMangling", [Cursor], _CXString, _CXString.from_result),
    # ("clang_getCXTUResourceUsage",
    #  [TranslationUnit],
    #  CXTUResourceUsage),
    ("clang_getCXXAccessSpecifier", [Cursor], ctypes.c_uint),
    ("clang_getDeclObjCTypeEncoding", [Cursor], _CXString, _CXString.from_result),
    ("clang_getDiagnostic", [c_object_p, ctypes.c_uint], c_object_p),
    ("clang_getDiagnosticCategory", [Diagnostic], ctypes.c_uint),
    ("clang_getDiagnosticCategoryText", [Diagnostic], _CXString, _CXString.from_result),
    (
        "clang_getDiagnosticFixIt",
        [Diagnostic, ctypes.c_uint, ctypes.POINTER(SourceRange)],
        _CXString,
        _CXString.from_result,
    ),
    ("clang_getDiagnosticInSet", [c_object_p, ctypes.c_uint], c_object_p),
    ("clang_getDiagnosticLocation", [Diagnostic], SourceLocation),
    ("clang_getDiagnosticNumFixIts", [Diagnostic], ctypes.c_uint),
    ("clang_getDiagnosticNumRanges", [Diagnostic], ctypes.c_uint),
    (
        "clang_getDiagnosticOption",
        [Diagnostic, ctypes.POINTER(_CXString)],
        _CXString,
        _CXString.from_result,
    ),
    ("clang_getDiagnosticRange", [Diagnostic, ctypes.c_uint], SourceRange),
    ("clang_getDiagnosticSeverity", [Diagnostic], ctypes.c_int),
    ("clang_getDiagnosticSpelling", [Diagnostic], _CXString, _CXString.from_result),
    ("clang_getElementType", [Type], Type, Type.from_result),
    ("clang_getEnumConstantDeclUnsignedValue", [Cursor], ctypes.c_ulonglong),
    ("clang_getEnumConstantDeclValue", [Cursor], ctypes.c_longlong),
    ("clang_getEnumDeclIntegerType", [Cursor], Type, Type.from_result),
    ("clang_getFile", [TranslationUnit, ctypes.c_char_p], c_object_p),
    ("clang_getFileName", [File], _CXString, _CXString.from_result),
    ("clang_getFileTime", [File], ctypes.c_uint),
    ("clang_getIBOutletCollectionType", [Cursor], Type, Type.from_result),
    ("clang_getIncludedFile", [Cursor], c_object_p, File.from_result),
    (
        "clang_getInclusions",
        [TranslationUnit, callbacks["translation_unit_includes"], ctypes.py_object],
    ),
    (
        "clang_getInstantiationLocation",
        [
            SourceLocation,
            ctypes.POINTER(c_object_p),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ],
    ),
    (
        "clang_getLocation",
        [TranslationUnit, File, ctypes.c_uint, ctypes.c_uint],
        SourceLocation,
    ),
    (
        "clang_getLocationForOffset",
        [TranslationUnit, File, ctypes.c_uint],
        SourceLocation,
    ),
    ("clang_getNullCursor", None, Cursor),
    ("clang_getNumArgTypes", [Type], ctypes.c_uint),
    ("clang_getNumCompletionChunks", [ctypes.c_void_p], ctypes.c_int),
    ("clang_getNumDiagnostics", [c_object_p], ctypes.c_uint),
    ("clang_getNumDiagnosticsInSet", [c_object_p], ctypes.c_uint),
    ("clang_getNumElements", [Type], ctypes.c_longlong),
    ("clang_getNumOverloadedDecls", [Cursor], ctypes.c_uint),
    (
        "clang_getOverloadedDecl",
        [Cursor, ctypes.c_uint],
        Cursor,
        Cursor.from_cursor_result,
    ),
    ("clang_getPointeeType", [Type], Type, Type.from_result),
    ("clang_getRange", [SourceLocation, SourceLocation], SourceRange),
    ("clang_getRangeEnd", [SourceRange], SourceLocation),
    ("clang_getRangeStart", [SourceRange], SourceLocation),
    ("clang_getResultType", [Type], Type, Type.from_result),
    ("clang_getSpecializedCursorTemplate", [Cursor], Cursor, Cursor.from_cursor_result),
    ("clang_getTemplateCursorKind", [Cursor], ctypes.c_uint),
    ("clang_getTokenExtent", [TranslationUnit, Token], SourceRange),
    ("clang_getTokenKind", [Token], ctypes.c_uint),
    ("clang_getTokenLocation", [TranslationUnit, Token], SourceLocation),
    (
        "clang_getTokenSpelling",
        [TranslationUnit, Token],
        _CXString,
        _CXString.from_result,
    ),
    ("clang_getTranslationUnitCursor", [TranslationUnit], Cursor, Cursor.from_result),
    (
        "clang_getTranslationUnitSpelling",
        [TranslationUnit],
        _CXString,
        _CXString.from_result,
    ),
    (
        "clang_getTUResourceUsageName",
        [ctypes.c_uint],
        ctypes.c_char_p,
        c_char_p_to_string,
    ),
    ("clang_getTypeDeclaration", [Type], Cursor, Cursor.from_result),
    ("clang_getTypedefDeclUnderlyingType", [Cursor], Type, Type.from_result),
    ("clang_getTypedefName", [Type], _CXString, _CXString.from_result),
    ("clang_getTypeKindSpelling", [ctypes.c_uint], _CXString, _CXString.from_result),
    ("clang_getTypeSpelling", [Type], _CXString, _CXString.from_result),
    ("clang_hashCursor", [Cursor], ctypes.c_uint),
    ("clang_isAttribute", [CursorKind], bool),
    ("clang_isConstQualifiedType", [Type], bool),
    ("clang_isCursorDefinition", [Cursor], bool),
    ("clang_isDeclaration", [CursorKind], bool),
    ("clang_isExpression", [CursorKind], bool),
    ("clang_isFileMultipleIncludeGuarded", [TranslationUnit, File], bool),
    ("clang_isFunctionTypeVariadic", [Type], bool),
    ("clang_isInvalid", [CursorKind], bool),
    ("clang_isPODType", [Type], bool),
    ("clang_isPreprocessing", [CursorKind], bool),
    ("clang_isReference", [CursorKind], bool),
    ("clang_isRestrictQualifiedType", [Type], bool),
    ("clang_isStatement", [CursorKind], bool),
    ("clang_isTranslationUnit", [CursorKind], bool),
    ("clang_isUnexposed", [CursorKind], bool),
    ("clang_isVirtualBase", [Cursor], bool),
    ("clang_isVolatileQualifiedType", [Type], bool),
    (
        "clang_parseTranslationUnit",
        [
            Index,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ],
        c_object_p,
    ),
    (
        "clang_reparseTranslationUnit",
        [TranslationUnit, ctypes.c_int, ctypes.c_void_p, ctypes.c_int],
        ctypes.c_int,
    ),
    (
        "clang_saveTranslationUnit",
        [TranslationUnit, ctypes.c_char_p, ctypes.c_uint],
        ctypes.c_int,
    ),
    (
        "clang_tokenize",
        [
            TranslationUnit,
            SourceRange,
            ctypes.POINTER(ctypes.POINTER(Token)),
            ctypes.POINTER(ctypes.c_uint),
        ],
    ),
    (
        "clang_visitChildren",
        [Cursor, callbacks["cursor_visit"], ctypes.py_object],
        ctypes.c_uint,
    ),
    ("clang_Cursor_getNumArguments", [Cursor], ctypes.c_int),
    ("clang_Cursor_getArgument", [Cursor, ctypes.c_uint], Cursor, Cursor.from_result),
    ("clang_Cursor_getNumTemplateArguments", [Cursor], ctypes.c_int),
    (
        "clang_Cursor_getTemplateArgumentKind",
        [Cursor, ctypes.c_uint],
        TemplateArgumentKind.from_id,
    ),
    (
        "clang_Cursor_getTemplateArgumentType",
        [Cursor, ctypes.c_uint],
        Type,
        Type.from_result,
    ),
    (
        "clang_Cursor_getTemplateArgumentValue",
        [Cursor, ctypes.c_uint],
        ctypes.c_longlong,
    ),
    (
        "clang_Cursor_getTemplateArgumentUnsignedValue",
        [Cursor, ctypes.c_uint],
        ctypes.c_ulonglong,
    ),
    ("clang_Cursor_isAnonymous", [Cursor], bool),
    ("clang_Cursor_isBitField", [Cursor], bool),
    ("clang_Cursor_getBriefCommentText", [Cursor], _CXString, _CXString.from_result),
    ("clang_Cursor_getRawCommentText", [Cursor], _CXString, _CXString.from_result),
    ("clang_Cursor_getOffsetOfField", [Cursor], ctypes.c_longlong),
    ("clang_Type_getAlignOf", [Type], ctypes.c_longlong),
    ("clang_Type_getClassType", [Type], Type, Type.from_result),
    ("clang_Type_getNumTemplateArguments", [Type], ctypes.c_int),
    (
        "clang_Type_getTemplateArgumentAsType",
        [Type, ctypes.c_uint],
        Type,
        Type.from_result,
    ),
    ("clang_Type_getOffsetOf", [Type, ctypes.c_char_p], ctypes.c_longlong),
    ("clang_Type_getSizeOf", [Type], ctypes.c_longlong),
    ("clang_Type_getCXXRefQualifier", [Type], ctypes.c_uint),
    ("clang_Type_getNamedType", [Type], Type, Type.from_result),
    (
        "clang_Type_visitFields",
        [Type, callbacks["fields_visit"], ctypes.py_object],
        ctypes.c_uint,
    ),
    (
        "clang_getCursorPlatformAvailability",
        [
            Cursor,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(_CXString),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(_CXString),
            ctypes.POINTER(PlatformAvailability),
        ],
        ctypes.c_int,
    ),
    (
        "clang_disposeCXPlatformAvailability",
        [ctypes.POINTER(PlatformAvailability)],
        None,
    ),
    ("clang_getCursorLinkage", [Cursor], ctypes.c_int),
    ("clang_Cursor_isObjCOptional", [Cursor], ctypes.c_uint),
    ("clang_getCXXAccessSpecifier", [Cursor], ctypes.c_int),
    ("clang_Cursor_isVariadic", [Cursor], ctypes.c_uint),
    ("clang_Cursor_getObjCDeclQualifiers", [Cursor], ctypes.c_uint),
    ("clang_Cursor_getObjCPropertyAttributes", [Cursor, ctypes.c_uint], ctypes.c_uint),
    ("clang_getCursorResultType", [Cursor], Type, Type.from_result),
    ("clang_Type_getNullability", [Type], ctypes.c_uint),
    ("clang_Type_getModifiedType", [Type], Type),
]


class LibclangError(Exception):
    def __init__(self, message):
        self.m = message

    def __str__(self):
        return self.m


def register_function(lib, item, ignore_errors):
    # A function may not exist, if these bindings are used with an older or
    # incompatible version of libclang.so.
    try:
        func = getattr(lib, item[0])
    except AttributeError as e:
        msg = (
            str(e) + ". Please ensure that your python bindings are "
            "compatible with your libclang.so version."
        )
        if ignore_errors:
            return
        raise LibclangError(msg)

    if len(item) >= 2:
        func.argtypes = item[1]

    if len(item) >= 3:
        func.restype = item[2]

    if len(item) == 4:
        func.errcheck = item[3]


def register_functions(lib, ignore_errors):
    """Register function prototypes with a libclang library instance.

    This must be called as part of library instantiation so Python knows how
    to call out to the shared library.
    """

    def register(item):
        return register_function(lib, item, ignore_errors)

    for f in functionList:
        register(f)


class Config(object):
    library_path: typing.Optional[str] = None
    library_file: typing.Optional[str] = None
    compatibility_check: bool = True
    loaded: bool = False

    @classmethod
    def set_library_path(cls, path: typing.Union[str, os.PathLike[str]]) -> None:
        """Set the path in which to search for libclang"""
        if cls.loaded:
            raise Exception(
                "library path must be set before before using "
                "any other functionalities in libclang."
            )

        cls.library_path = os.fspath(path)

    @classmethod
    def set_library_file(cls, filename: typing.Union[str, os.PathLike[str]]) -> None:
        """Set the exact location of libclang"""
        if cls.loaded:
            raise Exception(
                "library file must be set before before using "
                "any other functionalities in libclang."
            )

        cls.library_file = os.fspath(filename)

    @classmethod
    def set_compatibility_check(cls, check_status: bool) -> None:
        """Perform compatibility check when loading libclang

        The python bindings are only tested and evaluated with the version of
        libclang they are provided with. To ensure correct behavior a (limited)
        compatibility check is performed when loading the bindings. This check
        will throw an exception, as soon as it fails.

        In case these bindings are used with an older version of libclang, parts
        that have been stable between releases may still work. Users of the
        python bindings can disable the compatibility check. This will cause
        the python bindings to load, even though they are written for a newer
        version of libclang. Failures now arise if unsupported or incompatible
        features are accessed. The user is required to test themselves if the
        features they are using are available and compatible between different
        libclang versions.
        """
        if cls.loaded:
            raise Exception(
                "compatibility_check must be set before before "
                "using any other functionalities in libclang."
            )

        cls.compatibility_check = check_status

    @CachedProperty
    def lib(self):
        lib = self.get_cindex_library()
        register_functions(lib, not Config.compatibility_check)
        Config.loaded = True
        return lib

    def get_filename(self) -> str:
        if Config.library_file:
            return Config.library_file

        import platform

        name = platform.system()

        if name == "Darwin":
            name = "libclang.dylib"
        elif name == "Windows":
            name = "libclang.dll"
        else:
            name = "libclang-11.so"

        if Config.library_path:
            name = Config.library_path + "/" + name

        return name

    def get_cindex_library(self) -> ctypes.CDLL:
        try:
            library = ctypes.cdll.LoadLibrary(self.get_filename())
        except OSError as e:
            msg = (
                str(e) + ". To provide a path to libclang use "
                "Config.set_library_path() or "
                "Config.set_library_file()."
            )
            raise LibclangError(msg)

        return library

    def function_exists(self, name: str) -> bool:
        try:
            getattr(self.lib, name)
        except AttributeError:
            return False

        return True


TokenKinds = [
    ("PUNCTUATION", 0),
    ("KEYWORD", 1),
    ("IDENTIFIER", 2),
    ("LITERAL", 3),
    ("COMMENT", 4),
]


def register_enumerations():
    # XXX: This should be an IntEnum
    for name, value in TokenKinds:
        TokenKind.register(value, name)


conf = Config()
register_enumerations()

__all__ = [
    "AvailabilityKind",
    "Config",
    "CodeCompletionResults",
    "CompilationDatabase",
    "CompileCommands",
    "CompileCommand",
    "CursorKind",
    "Cursor",
    "Diagnostic",
    "File",
    "FixIt",
    "Index",
    "LinkageKind",
    "SourceLocation",
    "SourceRange",
    "TLSKind",
    "TokenKind",
    "Token",
    "TranslationUnitLoadError",
    "TranslationUnit",
    "TypeKind",
    "Type",
]
