import pathlib
import unittest

import objc
from objective.metadata import datamodel, parsing, xcode

FRAGMENTS = pathlib.Path(__file__).parent / "fragments.framework"


class TestParsingEnum(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parsing.FrameworkParser(
            "fragments",
            start_header="enum.h",
            sdk=xcode.path_for_default_sdk(),
            cflags=["-isystem", str(FRAGMENTS)],
        )
        cls.parsed.parse()

    def assert_enum_type(
        self,
        *,
        name,
        encoding,
        flags=False,
        unavailable=None,
        suggestion=None,
        introduced=None,
        deprecated=None,
        deprecated_message=None,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.enum_type)
        info = self.parsed.meta.enum_type[name]
        self.assertEqual(info.typestr, encoding)
        self.assertIs(info.flags, flags)
        self.assertEqual(
            info.availability,
            datamodel.AvailabilityInfo(
                unavailable, suggestion, introduced, deprecated, deprecated_message
            ),
        )
        self.assertIs(info.ignore, ignore)

    def assert_enum_value(
        self,
        *,
        name,
        value,
        enum_type=None,
        unavailable=None,
        suggestion=None,
        introduced=None,
        deprecated=None,
        deprecated_message=None,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.enum)
        info = self.parsed.meta.enum[name]

        self.assertEqual(info.value, value)
        self.assertEqual(info.enum_type, enum_type)
        self.assertEqual(
            info.availability,
            datamodel.AvailabilityInfo(
                unavailable, suggestion, introduced, deprecated, deprecated_message
            ),
        )
        self.assertIs(info.ignore, ignore)

    def test_BasicEnum(self):
        self.assert_enum_type(name="BasicEnum", encoding=objc._C_NSInteger)
        self.assert_enum_value(name="BasicEnumValue1", value=0, enum_type="BasicEnum")
        self.assert_enum_value(name="BasicEnumValue2", value=1, enum_type="BasicEnum")
        self.assert_enum_value(name="BasicEnumValue3", value=2, enum_type="BasicEnum")

    def test_ValuedEnum(self):
        self.assert_enum_type(name="ValuedEnum", encoding=objc._C_NSUInteger)
        self.assert_enum_value(name="ValuedEnumValue1", value=1, enum_type="ValuedEnum")
        self.assert_enum_value(name="ValuedEnumValue2", value=2, enum_type="ValuedEnum")
        self.assert_enum_value(name="ValuedEnumValue3", value=3, enum_type="ValuedEnum")

    def test_DeprecatedEnumValue(self):
        self.assert_enum_type(name="DeprecatedEnumValue", encoding=objc._C_NSInteger)
        self.assert_enum_value(
            name="DeprecatedEnumValue1", value=1, enum_type="DeprecatedEnumValue"
        )
        self.assert_enum_value(
            name="DeprecatedEnumValue2",
            value=2,
            enum_type="DeprecatedEnumValue",
            introduced=1006,
            deprecated=100900,
        )

    def test_DeprecatedEnum(self):
        self.assert_enum_type(
            name="DeprecatedEnum",
            encoding=objc._C_NSInteger,
            introduced=1000,
            deprecated=101100,
        )
        self.assert_enum_value(
            name="DeprecatedEnum1",
            value=0,
            enum_type="DeprecatedEnum",
            introduced=1000,
            deprecated=101100,
        )

    def test_BasicOptions(self):
        self.assert_enum_type(
            name="BasicOptions", encoding=objc._C_NSUInteger, flags=True
        )
        self.assert_enum_value(name="Option1", value=1 << 1, enum_type="BasicOptions")
        self.assert_enum_value(name="Option2", value=1 << 2, enum_type="BasicOptions")
        self.assert_enum_value(name="Option3", value=1 << 3, enum_type="BasicOptions")
        self.assert_enum_value(name="Option8", value=1 << 8, enum_type="BasicOptions")

    def test_CUnnamedEnum(self):
        self.assert_enum_type(name="CUnnamedEnum", encoding=objc._C_UINT)
        self.assert_enum_value(name="v1", value=1, enum_type="CUnnamedEnum")
        self.assert_enum_value(name="v2", value=2, enum_type="CUnnamedEnum")

    def test_CNamedEnum(self):
        self.assert_enum_type(name="CNamedEnum", encoding=objc._C_UINT)
        self.assert_enum_value(name="nv1", value=1, enum_type="CNamedEnum")
        self.assert_enum_value(name="nv2", value=2, enum_type="CNamedEnum")

        self.assertNotIn("C_Named_Enum", self.parsed.meta.enum_type)

    def test_completely_unamed_enum(self):
        self.assert_enum_value(name="value_in_unnamed_enum1", value=1, enum_type="")
        self.assert_enum_value(name="value_in_unnamed_enum2", value=2, enum_type="")
