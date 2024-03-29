""""
Datamodel for the metadata scanner.

This file is currently incomplete. The following needs to be added
    - Add types and additional fields for exception files
    - Add helper methods for extracting exception information
      from the model.
    - Deal with alternatives (sets of scanned files that have different
      definitions, for example constants that aren't the same
      for x86_64 and arm64)
"""
import json
import os
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    Tuple,
    TypedDict,
    TypeVar,
    Union,
)

from dataclasses_json import Undefined, config, dataclass_json

from . import util

FILE_TYPE = Union[str, os.PathLike[str]]


def bytes_decode(value: Union[None, str, List[str]]) -> Optional[bytes]:
    if isinstance(value, list):
        # Backward compatibility with existing metadata exceptions:
        # [ 32-bit, 64-bit], use only 64-bit value.
        #
        # To be removed when PyObjC is converted to the new tooling.
        value = value[-1]

    elif value is None:
        return None

    return value.encode("ascii")


bytes_config = config(
    encoder=lambda value: None if value is None else value.decode("ascii"),
    decoder=bytes_decode,
)

argdict_config = config(
    encoder=lambda value: None if value is None else {str(k) for k, v in value.items()},
    decoder=lambda value: None if value is None else {int(k) for k, v in value.items()},
    # The field_name in existing metadata exception files needs to be changed,
    # the alternative name will be removed when PyObjC is converted to the new
    # tooling.
    field_name="arguments",
)

T = TypeVar("T")


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class MergedInfo(Generic[T]):
    """
    At a number of places information
    will be different between different
    CPU architectures.

    This generic class is used to represent
    those cases.
    """

    # Value on x86_64 architecture
    x86_64: T

    # Value on arm64 architecture
    arm64: T


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class AvailabilityInfo:
    """Information about API availability"""

    # API is unavailable for some reason
    unavailable: Optional[bool] = None

    # Explict suggestion for unavailable APIs
    suggestion: Optional[str] = None

    # macOS version where the API was introduced,
    # encoded in an integer (format is the same
    # as the MAC_OS_VERSION_... constants)
    introduced: Optional[int] = None

    # macOS version where the API was deprecated,
    # encoded in an integer.
    deprecated: Optional[int] = None

    # Explicit message with a deprecated API.
    deprecated_message: Optional[str] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class EnumTypeInfo:
    """Information about a C enum type"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # True if this is a flag enum
    flags: bool = False

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class EnumInfo:
    """Information about individual enum labels"""

    # Value for this enum label
    value: Union[int, MergedInfo[int]]

    # Name of the associated enum type
    # This is optional for exception data, should
    # be set otherwise!
    enum_type: Optional[str] = None

    # API availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class StructInfo:
    """Information about a C structure"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # If true the *typestr* is not the same as
    # what will be seen in the ObjC Runtime.
    #
    # Primarily used to mark anonymous structs
    # where the metadata will contain a
    # generated name.
    typestr_special: bool

    # List of names for the struct fields
    fieldnames: List[str]

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class ExternInfo:
    """Information about C global variable (constants)"""

    # Type encoding (as used by PyObjC)
    typestr: Union[bytes, MergedInfo[bytes]] = field(metadata=bytes_config)

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # If true the value is a magic value. This is used with
    # _C_ID values where the pointer itself is special and
    # not a reference to an ObjC object.
    magic_cookie: bool = False

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


#
# Information about callables. The data structure can represent
# functions/methods with a callback (function or block) as one
# of its arguments, where one of the arguments for the callback
# has itself an argument that is a callback.
#
# The nested callback can not have callback arguments. This is
# primarily because the tooling used does not support this without
# more code replication, and the current level of nesting is good
# enough.
#


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class CallbackArgInfo2:
    """Information about the argument of a callback to a callback"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # Is *NULL* and acceptable value for the C API?
    # - True: yes
    # - False: no
    # - None: don't know (treated as True by PyObjC)
    null_accepted: Optional[bool] = None

    # Used with pointers to _C_ID: If *true* a value
    # returned through this argument is already retained
    # (caller has to call -release when it no longer
    # needds the value).
    already_retained: Optional[bool] = False

    # Used with pointers to CoreFoundation objects.
    # If *true* a value returned through this argument is
    # already retained # (caller has to call CFRelease when
    # it no longer needds the value).
    already_cfretained: Optional[bool] = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class CallbackInfo2:
    """Information about a callback signature for a callback"""

    # Information about the return value
    retval: Optional[CallbackArgInfo2]

    # Information about all arguments
    args: List[CallbackArgInfo2]

    # Is this a variadic function?
    variadic: bool = False

    # Which of the fixed arguments for a varidic function
    # is a printf format string (if any)
    printf_format: Optional[int] = None

    # Does this variadic function have a number of
    # arguments of the same time, ending with a NUL value
    # for the type?
    null_terminated: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class CallbackArgInfo:
    """Information about the argument of a callback"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # True if *typestr* is not the same as the value
    # in the ObjC runtime.
    typestr_special: bool = False

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # Is *NULL* and acceptable value for the C API?
    # - True: yes
    # - False: no
    # - None: don't know (treated as True by PyObjC)
    null_accepted: Optional[bool] = None

    # Used with pointers to _C_ID: If *true* a value
    # returned through this argument is already retained
    # (caller has to call -release when it no longer
    # needds the value).
    already_retained: Optional[bool] = False

    # Used with pointers to CoreFoundation objects.
    # If *true* a value returned through this argument is
    # already retained # (caller has to call CFRelease when
    # it no longer needds the value).
    already_cfretained: Optional[bool] = False

    # Information about the signature for a argument
    # that is a block or function.
    callable: Optional[CallbackInfo2] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class CallbackInfo:
    """Information about a callback signature"""

    # Information about the return value
    retval: CallbackArgInfo

    # Information about all arguments
    args: List[CallbackArgInfo] = field(metadata=config(field_name="arguments"))

    # Is this a variadic function?
    variadic: bool = False

    # Which of the fixed arguments for a varidic function
    # is a printf format string (if any)
    printf_format: Optional[int] = None

    # Does this variadic function have a number of
    # arguments of the same time, ending with a NUL value
    # for the type?
    null_terminated: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class ArgInfo:
    """Information about a function/method argument"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # Name of the argument
    name: Optional[str] = None

    # True if *typestr* is not the same as the value
    # in the ObjC runtime.
    typestr_special: bool = False

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # For pointer arguments (_C_PTR) mark if the data is
    # passed in (_C_IN), out (_C_OUT) or both (_C_INOUT).
    type_modifier: Optional[bytes] = field(metadata=bytes_config, default=None)

    # For pointer (_C_PTR) or object (_C_ID) arguments
    # tell if NULL is an acceptable value for the argument in C.
    null_accepted: Optional[bool] = None

    # If true this argument is a printf format string for
    # variadic callable.
    printf_format: bool = False

    # Used with pointers to _C_ID: If *true* a value
    # returned through this argument is already retained
    # (caller has to call -release when it no longer
    # needds the value).
    already_retained: Optional[bool] = False

    # Used with pointers to CoreFoundation objects.
    # If *true* a value returned through this argument is
    # already retained # (caller has to call CFRelease when
    # it no longer needds the value).
    already_cfretained: Optional[bool] = False

    # Signature information for a block or function argument
    callable: Optional[CallbackInfo] = None

    # True if the callable is stored by the called API
    callable_retained: Optional[bool] = None

    # If the argument is a C array with the size in another
    # argument this option describes which argument contains
    # the size.
    # - value: The argument containing the size
    # - (in_value, out_value); The *in_value*-th argument
    #   contains the size of the array before the call, the
    #   *out_value* contains the (effective) size after the call.
    c_array_length_in_arg: Optional[Union[int, Tuple[int, int]]] = None

    # If true the argument is a C array (with a length specified by one
    # of the other options). The effective length of the array on return
    # is in the return value of the function/method.
    c_array_length_in_result: bool = False

    # If true the argument is a C-array for which the
    # required size is either not known or cannot be described
    # by the metadata system.
    c_array_of_variable_length: bool = False

    # If true the argument is a C-array with the type-appropriated
    # NUL-value at the end. Python users won't use the delimiter.
    c_array_delimited_by_null: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class ArgExceptionInfo:
    """Information about a function/method argument"""

    # Can there be a shared superclass with ArgInfo?

    # Type encoding (as used by PyObjC)
    typestr: Optional[bytes] = field(metadata=bytes_config, default=None)

    # Name of the argument
    name: Optional[str] = None

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # For pointer arguments (_C_PTR) mark if the data is
    # passed in (_C_IN), out (_C_OUT) or both (_C_INOUT).
    type_modifier: Optional[bytes] = field(metadata=bytes_config, default=None)

    # For pointer (_C_PTR) or object (_C_ID) arguments
    # tell if NULL is an acceptable value for the argument in C.
    null_accepted: Optional[bool] = None

    # If true this argument is a printf format string for
    # variadic callable.
    printf_format: bool = False

    # Used with pointers to _C_ID: If *true* a value
    # returned through this argument is already retained
    # (caller has to call -release when it no longer
    # needds the value).
    already_retained: Optional[bool] = None

    # Used with pointers to CoreFoundation objects.
    # If *true* a value returned through this argument is
    # already retained # (caller has to call CFRelease when
    # it no longer needds the value).
    already_cfretained: Optional[bool] = None

    # Signature information for a block or function argument
    callable: Optional[CallbackInfo] = None

    # True if the callable is stored by the called API
    callable_retained: Optional[bool] = None

    # If the argument is a C array with the size in another
    # argument this option describes which argument contains
    # the size.
    # - value: The argument containing the size
    # - (in_value, out_value); The *in_value*-th argument
    #   contains the size of the array before the call, the
    #   *out_value* contains the (effective) size after the call.
    c_array_length_in_arg: Optional[Union[int, Tuple[int, int]]] = None

    # If true the argument is a C array (with a length specified by one
    # of the other options). The effective length of the array on return
    # is in the return value of the function/method.
    c_array_length_in_result: Optional[bool] = None

    # If true the argument is a C-array for which the
    # required size is either not known or cannot be described
    # by the metadata system.
    c_array_of_variable_length: Optional[bool] = None

    # If true the argument is a C-array with the type-appropriated
    # NUL-value at the end. Python users won't use the delimiter.
    c_array_delimited_by_null: Optional[bool] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class ReturnInfo:
    """Information about a function/method return value"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # True if *typestr* is not the same as the value
    # in the ObjC runtime.
    typestr_special: bool = False

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # For pointer (_C_PTR) or object (_C_ID) arguments
    # tell if NULL is an acceptable value for the argument in C.
    null_accepted: Optional[bool] = None

    # Used with pointers to _C_ID: If *true* a value
    # returned through this argument is already retained
    # (caller has to call -release when it no longer
    # needds the value).
    already_retained: Optional[bool] = False

    # Used with pointers to CoreFoundation objects.
    # If *true* a value returned through this argument is
    # already retained # (caller has to call CFRelease when
    # it no longer needds the value).
    already_cfretained: Optional[bool] = False

    # Signature information for a block or function argument
    callable: Optional[CallbackInfo] = None

    # If true the argument is a C-array for which the
    # required size is either not known or cannot be described
    # by the metadata system.
    c_array_of_variable_length: bool = False

    # If true the argument is a C-array with the type-appropriated
    # NUL-value at the end. Python users won't use the delimiter.
    c_array_delimited_by_null: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass(frozen=True)
class ReturnExceptionInfo:
    """Information about a function/method return value"""

    # Type encoding (as used by PyObjC)
    typestr: Optional[bytes] = field(metadata=bytes_config, default=None)

    # For pointer (_C_PTR) or object (_C_ID) arguments
    # tell if NULL is an acceptable value for the argument in C.
    null_accepted: Optional[bool] = None

    # Used with pointers to _C_ID: If *true* a value
    # returned through this argument is already retained
    # (caller has to call -release when it no longer
    # needds the value).
    already_retained: Optional[bool] = False

    # Used with pointers to CoreFoundation objects.
    # If *true* a value returned through this argument is
    # already retained # (caller has to call CFRelease when
    # it no longer needds the value).
    already_cfretained: Optional[bool] = False

    # Signature information for a block or function argument
    callable: Optional[CallbackInfo] = None

    # If true the argument is a C-array for which the
    # required size is either not known or cannot be described
    # by the metadata system.
    c_array_of_variable_length: Optional[bool] = None

    # If true the argument is a C-array with the type-appropriated
    # NUL-value at the end. Python users won't use the delimiter.
    c_array_delimited_by_null: Optional[bool] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class FunctionInfo:
    """Information about a function"""

    # Information about the return value
    retval: ReturnInfo

    # Information about all arguments
    args: List[ArgInfo]

    # Is this an inline function?
    inline: bool = False

    # Is this a variadic method
    variadic: bool = False

    @property
    def is_k_and_r(self):
        """ K&R style declaration """
        return self.variadic and not self.args

    # Argument index for a variadic function
    # that is the printf_format (if any)
    printf_format: Optional[int] = None

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # To be removed
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class FunctionExceptionInfo:
    """Exception data for a function"""

    # Information about the return value
    retval: Optional[ReturnExceptionInfo] = None

    # Information about all arguments
    args: Optional[Dict[int, ArgExceptionInfo]] = field(
        default=None, metadata=argdict_config
    )

    # Is this an inline function?
    inline: bool = False

    # Is this a variadic method
    variadic: bool = False

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class MethodInfo:
    """Information about a method in an ObjC class or protocol"""

    # Selector for the method
    selector: str

    # Is this a class method?
    class_method: bool

    # Information about the return value
    retval: ReturnInfo

    # Information about all arguments
    args: List[ArgInfo]

    # Used in protocols: is this a required method
    # (maybe split into two classes?)
    required: Optional[bool] = None

    # Is this a variadic method
    variadic: bool = False

    # Argument index for a variadic function
    # that is the printf_format (if any)
    printf_format: Optional[int] = None

    # Category that this method is defined in
    # (*None* for methods in the main class
    # definition)
    category: Optional[str] = None

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    # To be removed
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class MethodExceptionInfo:
    """Override Information about a method in an ObjC class or protocol"""

    # Selector for the method
    selector: str

    # Is this a class method?
    class_method: bool

    # Information about the return value
    retval: Optional[ReturnExceptionInfo] = None

    # Information about all arguments
    args: Optional[Dict[int, ArgExceptionInfo]] = field(
        default=None, metadata=argdict_config
    )

    # Is this a variadic method
    variadic: Optional[bool] = None

    # Argument index for a variadic function
    # that is the printf_format (if any)
    printf_format: Optional[int] = None

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class PropertyInfo:
    """class property"""

    # Name of the property
    name: str

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # True if *typestr* is not the same as the value
    # in the ObjC runtime.
    typestr_special: bool

    # Name of the type associated with *typestr*, will
    # by used when there is a better type name than
    # the default.
    type_name: Optional[str] = None

    # Name of getter if it is not default
    getter: Optional[str] = None

    # Name of setter if it is not default
    setter: Optional[str] = None

    # Property attributes (such as readonline, readwrite)
    # ... -> should extract custom getter/setter into its
    #        own attribute
    attributes: Set[str] = field(default_factory=util.sorted_set)

    # Category that this method is defined in
    # (*None* for methods in the main class
    # definition)
    category: Optional[str] = None

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class ProtocolInfo:
    """Objective-C protocol, or deduced informal protocol (category)"""

    # List of names for protocols this protocol
    # implements (subclassing for protocols)
    implements: List[str]

    # List of methods in this protocol
    methods: List[MethodInfo]

    # List of properties in this protocol
    properties: List[PropertyInfo]

    # API Availability
    availability: Optional[AvailabilityInfo] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class ClassInfo:
    """Objective-C class"""

    # Superclass for this class.
    # This will be *None* in two cases:
    # - Root classes (NSObject/NSProxy)
    # - Categories on classes defined in another framework
    super: Optional[str]

    # List of names for protocols this protocol
    # implements (subclassing for protocols)
    implements: List[str] = field(metadata=config(field_name="protocols"))

    # List of methods in this protocol
    methods: List[MethodInfo]

    # List of properties in this protocol
    properties: List[PropertyInfo]

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    final: bool = False

    # Categories defined on this class
    categories: Set[str] = field(default_factory=util.sorted_set)


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class ClassExceptionInfo:
    """Objective-C class"""

    # Superclass for this class.
    # This will be *None* in two cases:
    # - Root classes (NSObject/NSProxy)
    # - Categories on classes defined in another framework
    super: Optional[str]

    # List of names for protocols this protocol
    # implements (subclassing for protocols)
    implements: List[str] = field(metadata=config(field_name="protocols"))

    # List of methods in this protocol
    methods: List[MethodExceptionInfo] = field(default_factory=list)

    # List of properties in this protocol
    properties: List[PropertyInfo] = field(default_factory=list)

    # API Availability
    availability: Optional[AvailabilityInfo] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class CFTypeInfo:
    """Information about a CoreFoundation type"""

    # Type encoding (as used by PyObjC)
    typestr: bytes = field(metadata=bytes_config)

    # Name of the ...GetTypeID function for this CFType
    # (can be None when autodetection fails)
    gettypeid_func: Optional[str] = None

    # Name of the ObjC class this type is tollfree bridged to.
    tollfree: Optional[str] = None

    # Used in exception information
    # Need to investigate why this is needed, should not happen!
    opaque: Optional[bool] = None


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class LiteralInfo:
    """Named literals other than enums"""

    # Value of the literal
    value: Union[None, int, float, str, MergedInfo[Union[None, int, float, str]]]

    # False if this "str" represents a byte literal
    unicode: Optional[bool] = False

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # For exception information: ignore this declaration
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class AliasInfo:
    """Name that aliases another name"""

    # What this name is an alias for
    alias: str

    # If this is an alias for an item in a named
    # enum this field contains that name.
    enum_type: Optional[str] = None

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class ExpressionInfo:
    """A C define that evaluates to a constant expression"""

    # The expression
    expression: str

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class FunctionMacroInfo:
    """
    A C define with parameters that can be evaluated as a Python function
    """

    # String that evaluates to the function
    definition: str

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # To be removed
    ignore: bool = False


class FUNCTION_MACRO_UPDATE_DICT(TypedDict, total=False):
    definition: str
    availability: AvailabilityInfo


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class FunctionMacroExceptionInfo:
    """
    A C define with parameters that can be evaluated as a Python function
    """

    # String that evaluates to the function
    definition: Optional[str]

    # API Availability
    availability: Optional[AvailabilityInfo] = None

    # Used to mark APIs as ignored in exception
    # files.
    ignore: bool = False

    def exception_info(self) -> FUNCTION_MACRO_UPDATE_DICT:
        result: FUNCTION_MACRO_UPDATE_DICT = {}
        if self.definition is not None:
            result["definition"] = self.definition
        if self.availability is not None:
            result["availability"] = self.availability
        return result


@dataclass_json(undefined=Undefined.RAISE)
@dataclass
class FrameworkMetadata:
    architectures: Set[str] = field(default_factory=util.sorted_set)
    sdk_version: Optional[str] = None
    enum_type: Dict[str, EnumTypeInfo] = field(default_factory=dict)
    enum: Dict[str, EnumInfo] = field(default_factory=dict)
    structs: Dict[str, StructInfo] = field(default_factory=dict)
    externs: Dict[str, ExternInfo] = field(default_factory=dict)
    cftypes: Dict[str, CFTypeInfo] = field(default_factory=dict)
    literals: Dict[str, LiteralInfo] = field(default_factory=dict)
    formal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    informal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    classes: Dict[str, ClassInfo] = field(default_factory=dict)
    aliases: Dict[str, AliasInfo] = field(default_factory=dict)
    expressions: Dict[str, ExpressionInfo] = field(default_factory=dict)
    func_macros: Dict[str, FunctionMacroInfo] = field(default_factory=dict)
    functions: Dict[str, FunctionInfo] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: FILE_TYPE) -> "FrameworkMetadata":
        with open(path, "r") as stream:
            raw_data = stream.read()

        # Strip optional leading comment block
        while raw_data.startswith("//"):
            _, _, raw_data = raw_data.partition("\n")

        data = json.loads(raw_data)
        del raw_data

        return FrameworkMetadata.from_dict(data["definitions"])

    def to_file(self, path: FILE_TYPE) -> None:
        data = self.to_dict()

        with open(path, "w") as stream:
            json.dump({"definitions": data}, stream)

    if TYPE_CHECKING:
        # Dataclasses_json adds (amongst others) methods from_dict and
        # to_dict which mypy doesn't know about.
        #
        # The declaration below teach the typechecker about these methods.

        @classmethod
        def from_dict(cls, value: dict) -> "FrameworkMetadata":
            ...

        def to_dict(self) -> dict:
            ...


@dataclass_json
@dataclass
class ExceptionData:
    """
    Exceptions data

    This is work in progress, at least some of the dataclasses
    should have variants for the exception file (required field
    in regular file, optional in exception file; likewise for the
    'ignored' field which is only used in exception data)
    """

    enum_type: Dict[str, EnumTypeInfo] = field(default_factory=dict)
    enum: Dict[str, EnumInfo] = field(default_factory=dict)
    # structs: Dict[str, StructInfo] = field(default_factory=dict)
    externs: Dict[str, ExternInfo] = field(default_factory=dict)
    # cftypes: Dict[str, CFTypeInfo] = field(default_factory=dict)
    literals: Dict[str, LiteralInfo] = field(default_factory=dict)
    # formal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    # informal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    # classes: Dict[str, ClassExceptionInfo] = field(default_factory=dict)
    aliases: Dict[str, AliasInfo] = field(default_factory=dict)
    expressions: Dict[str, ExpressionInfo] = field(default_factory=dict)
    func_macros: Dict[str, FunctionMacroExceptionInfo] = field(default_factory=dict)
    functions: Dict[str, FunctionExceptionInfo] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: FILE_TYPE) -> "ExceptionData":
        with open(path, "r") as stream:
            raw_data = stream.read()

        # Strip optional leading comment block
        while raw_data.startswith("//"):
            _, _, raw_data = raw_data.partition("\n")

        data = json.loads(raw_data)
        del raw_data

        if "formal_protocols" in data["definitions"]:
            del data["definitions"]["formal_protocols"]
        if "informal_protocols" in data["definitions"]:
            del data["definitions"]["informal_protocols"]
        if "cftypes" in data["definitions"]:
            del data["definitions"]["cftypes"]

        return ExceptionData.from_dict(data["definitions"], infer_missing=True)

    def to_file(self, path: FILE_TYPE) -> None:
        data = self.to_dict()

        with open(path, "w") as stream:
            json.dump({"definitions": data}, stream)

    if TYPE_CHECKING:
        # Dataclasses_json adds (amongst others) methods from_dict and
        # to_dict which mypy doesn't know about.
        #
        # The declaration below teach the typechecker about these methods.

        @classmethod
        def from_dict(cls, value: dict, infer_missing: bool = False) -> "ExceptionData":
            ...

        def to_dict(self) -> dict:
            ...
