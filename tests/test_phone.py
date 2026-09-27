"""Phone normalisation (PRD §11)."""

from django.test import SimpleTestCase, override_settings

from django_common_utils.phone import Phone

try:
    import phonenumbers  # noqa: F401
    HAS_LIBRARY = True
except ImportError:
    HAS_LIBRARY = False


class PhoneTests(SimpleTestCase):
    def setUp(self):
        if not HAS_LIBRARY:
            self.skipTest("phonenumbers is not installed")

    def test_national_number_normalises_against_the_default_region(self):
        self.assertEqual(Phone.normalise("0401773013"), "+61401773013")

    def test_a_foreign_number_keeps_its_own_country(self):
        self.assertEqual(Phone.normalise("+1 415 555 0132"), "+14155550132")

    def test_australian_international_access_code_is_understood(self):
        """0011 is AU's dial-out prefix; libphonenumber does not know it."""
        self.assertEqual(Phone.normalise("0011 44 20 7946 0958"), "+442079460958")

    def test_prose_is_not_a_phone_number(self):
        self.assertIsNone(Phone.normalise("ask for Dave"))

    def test_normalise_or_original_never_blanks_a_column(self):
        self.assertEqual(Phone.normalise_or_original("ask for Dave"), "ask for Dave")

    def test_aliases_of_one_number_share_an_identity(self):
        self.assertTrue(Phone.is_same_number("0412 345 678", "+61412345678"))

    def test_blank_is_never_the_same_number(self):
        self.assertFalse(Phone.is_same_number("", ""))

    @override_settings(DJANGO_COMMON_UTILS={"PHONE": {"SERVICEABLE_REGIONS": ["AU"]}})
    def test_a_foreign_number_is_valid_but_not_serviceable(self):
        self.assertIsNotNone(Phone.normalise("+1 415 555 0132"))
        self.assertFalse(Phone.is_serviceable("+1 415 555 0132"))

    @override_settings(DJANGO_COMMON_UTILS={"PHONE": {"SERVICEABLE_REGIONS": []}})
    def test_no_configured_regions_means_anywhere(self):
        self.assertTrue(Phone.is_serviceable("+1 415 555 0132"))

    def test_search_finds_a_stored_e164_from_a_typed_fragment(self):
        """'0400' is never a substring of '+61400000000'."""
        self.assertIn("61400", Phone.search_variants("0400"))

    def test_a_stored_e164_renders_the_way_a_local_writes_it(self):
        self.assertEqual(Phone.format_national("+61400000000"), "0400 000 000")

    def test_unreadable_input_formats_as_itself_trimmed(self):
        self.assertEqual(Phone.format_national("  ask for Dave "), "ask for Dave")

    def test_national_digits_drops_the_grouping(self):
        self.assertEqual(Phone.national_digits("+61400000000"), "0400000000")

    def test_national_digits_falls_back_to_the_raw_digits(self):
        """A caller matching on substrings still gets something to match."""
        self.assertEqual(Phone.national_digits("call 0400 x2"), "04002")

    def test_national_digits_of_blank_is_blank(self):
        self.assertEqual(Phone.national_digits(None), "")

    @override_settings(DJANGO_COMMON_UTILS={"PHONE": {"SERVICEABLE_REGIONS": ["AU"]}})
    def test_a_mobile_is_a_mobile(self):
        self.assertEqual(Phone.kind("0400 000 000"), Phone.MOBILE)
        self.assertTrue(Phone.is_mobile("0400 000 000"))

    @override_settings(DJANGO_COMMON_UTILS={"PHONE": {"SERVICEABLE_REGIONS": ["AU"]}})
    def test_a_landline_is_not_textable(self):
        self.assertEqual(Phone.kind("02 9374 4000"), Phone.LANDLINE)
        self.assertFalse(Phone.is_mobile("02 9374 4000"))

    @override_settings(DJANGO_COMMON_UTILS={"PHONE": {"SERVICEABLE_REGIONS": ["AU"]}})
    def test_a_toll_free_number_is_a_service_line(self):
        self.assertEqual(Phone.kind("1800 555 000"), Phone.SERVICE)

    @override_settings(DJANGO_COMMON_UTILS={"PHONE": {"SERVICEABLE_REGIONS": ["AU"]}})
    def test_an_unserviceable_number_has_no_kind(self):
        """Otherwise a caller is told 'mobile' about a number it cannot work."""
        self.assertIsNone(Phone.kind("+1 415 555 0132"))
        self.assertFalse(Phone.is_mobile("+1 415 555 0132"))

    def test_prose_has_no_kind(self):
        self.assertIsNone(Phone.kind("ask for Dave"))
