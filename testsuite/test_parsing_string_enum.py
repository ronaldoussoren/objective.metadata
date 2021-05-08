import pathlib
import unittest

import objc
from objective.metadata import datamodel, parsing, xcode

FRAGMENTS = pathlib.Path(__file__).parent / "fragments.framework"


class TestParsingStringEnum(unittest.TestCase):
    """
    typedef NSString* SomeType NS_STRING_ENUM;
    """

    @classmethod
    def setUpClass(cls):
        cls.parsed = parsing.FrameworkParser(
            "fragments",
            start_header="strenum.h",
            sdk=xcode.path_for_default_sdk(),
            cflags=["-isystem", str(FRAGMENTS)],
        )
        cls.parsed.parse()

    def assert_enum_type(
        self,
        *,
        name,
        encoding=objc._C_ID,
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
        encoding=objc._C_ID,
        enum_type=None,
        unavailable=None,
        suggestion=None,
        introduced=None,
        deprecated=None,
        deprecated_message=None,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.externs)
        info = self.parsed.meta.externs[name]

        self.assertEqual(info.typestr, encoding)

        # XXX: This data is not yet exposed...
        # self.assertEqual(info.enum_type, enum_type)
        self.assertEqual(
            info.availability,
            datamodel.AvailabilityInfo(
                unavailable, suggestion, introduced, deprecated, deprecated_message
            ),
        )
        self.assertIs(info.ignore, ignore)

    def test_StrEnum(self):
        # XXX: Annoyingly libclang doesn't expose the right information for this.
        # self.assert_enum_type(name="SomeStringEnum")
        self.assert_enum_value(name="SomeStringValue1", enum_type="SomeStringEnum")
        self.assert_enum_value(
            name="SomeStringValue2", enum_type="SomeStringEnum", introduced=1007
        )
        self.assert_enum_value(
            name="SomeStringValue3",
            enum_type="SomeStringEnum",
            introduced=1007,
            deprecated=1008,
            deprecated_message="message",
        )
