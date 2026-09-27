"""Phone number parsing, in one place, for any country (PRD §11).

Three different questions get asked about a phone number, and conflating them
is how a codebase ends up with four half-right parsers.

``normalise``              storage — "what is the one correct written form of
                           this number", any country, E.164 or nothing.
``normalise_serviceable``  validation — "is this a customer we can service",
                           which is a question about where we take work.
``contact_key``            identity — "are these two numbers the same person",
                           which rejects nothing at all.

None of them hard-code a country. Parsing a number written without a country
code needs *some* region to read it against, and where a business takes work is a
real rule, but both come from settings (``PHONE["DEFAULT_REGION"]``,
``PHONE["SERVICEABLE_REGIONS"]``). Expanding into a second country is a
settings change.

Everything is delegated to ``phonenumbers`` — Google's libphonenumber. Numbering
plans are not something to hand-roll: country codes run one to three digits,
trunk prefixes differ per country, and national lengths vary within a single
country.

**Without ``phonenumbers`` installed this module degrades rather than fails**,
and the degradation is worth stating: ``normalise`` falls back to checking E.164
*syntax*, which cannot tell that a well-formed string is undialable. That is the
shape that produced a fortnight of billed Twilio ``21211`` rejections, so a
project that sends SMS installs the ``phone`` extra. Every call that degrades
logs once at WARNING.
"""

import logging
import re

from django_common_kit.conf import app_settings

logger = logging.getLogger(__name__)

PHONE_E164_ERROR = "Enter a valid phone number with country code (e.g. +61 400 000 000)"

#: Syntactic E.164: a leading +, a non-zero country digit, up to 15 digits total.
_E164_SYNTAX = re.compile(r"^\+[1-9]\d{6,14}$")

_phonenumbers_module = None
_warned_about_fallback = False


def _phonenumbers():
    """The library, or ``None``. Imported lazily — the package must import with
    ``phonenumbers`` absent (PRD §9.3's rule, applied here too)."""
    global _phonenumbers_module
    if _phonenumbers_module is None:
        try:
            import phonenumbers

            _phonenumbers_module = phonenumbers
        except ImportError:
            _phonenumbers_module = False
    return _phonenumbers_module or None


def _warn_fallback():
    global _warned_about_fallback
    if not _warned_about_fallback:
        logger.warning(
            "phonenumbers is not installed: phone validation is falling back to "
            "E.164 syntax only, which accepts numbers that cannot be dialled. "
            "Install django-common-kit[phone] before sending SMS."
        )
        _warned_about_fallback = True


class Phone:
    """Parse, validate and key phone numbers. All classmethods; no state."""

    #: Fallbacks for when settings have not been loaded — a management command
    #: run against a bare environment, say.
    DEFAULT_REGION = "AU"
    DEFAULT_SERVICEABLE_REGIONS = ("AU",)

    #: Coarse kinds, at the grain a form actually acts on. The libphonenumber
    #: type is finer-grained than anything worth branching on — what a caller
    #: needs to know is "can I text this" (mobile) and "is this a business
    #: service line" (toll-free, shared-cost), not FIXED_LINE_OR_MOBILE vs VOIP.
    MOBILE = "mobile"
    LANDLINE = "landline"
    SERVICE = "service"

    # -- policy -------------------------------------------------------------

    @classmethod
    def default_region(cls):
        """Region a number with no country code is read against."""
        return app_settings.get("PHONE", "DEFAULT_REGION") or cls.DEFAULT_REGION

    @classmethod
    def serviceable_regions(cls):
        """Regions we take work from. Empty means anywhere."""
        return tuple(app_settings.get("PHONE", "SERVICEABLE_REGIONS") or ())

    @classmethod
    def uncontactable_types(cls):
        """Number kinds nobody gives as a contact number, in any country.

        The hand-rolled Australian validator this replaced excluded the 19xx
        premium ranges by regex; libphonenumber classifies the same idea
        everywhere, so the rule generalises instead of needing a pattern per
        country. Toll-free and shared-cost (1800/1300 here, 0800 in the UK) stay
        accepted — a business customer legitimately hands one over.
        """
        library = _phonenumbers()
        if library is None:
            return ()
        return (
            library.PhoneNumberType.PREMIUM_RATE,
            library.PhoneNumberType.VOICEMAIL,
        )

    @classmethod
    def kind_by_type(cls):
        """libphonenumber types mapped onto :attr:`MOBILE`/`LANDLINE`/`SERVICE`.

        Built on demand rather than held as a class attribute: the keys are
        library constants, and a class attribute would need the import at module
        scope, which the package forbids for an optional SDK.
        """
        library = _phonenumbers()
        if library is None:
            return {}
        return {
            library.PhoneNumberType.MOBILE: cls.MOBILE,
            library.PhoneNumberType.FIXED_LINE: cls.LANDLINE,
            # A number the library cannot split between the two is called a
            # landline: it is reachable, which is what LANDLINE means, and
            # calling it a mobile would let an "is this textable" check pass a
            # number that may never receive an SMS.
            library.PhoneNumberType.FIXED_LINE_OR_MOBILE: cls.LANDLINE,
            library.PhoneNumberType.TOLL_FREE: cls.SERVICE,
            library.PhoneNumberType.SHARED_COST: cls.SERVICE,
            library.PhoneNumberType.UAN: cls.SERVICE,
        }

    @classmethod
    def country_code(cls, region=None):
        """Dialling code for a region, as digits — ``61`` for AU, ``44`` for GB."""
        library = _phonenumbers()
        if library is None:
            return ""
        code = library.country_code_for_region(region or cls.default_region())
        return str(code) if code else ""

    # -- parsing ------------------------------------------------------------

    @classmethod
    def parse(cls, value, region=None):
        """A validated number object, or ``None``.

        Shared by everything below so one reading of a number is used
        consistently — validation and storage cannot disagree about what a string
        means. Returns ``None`` when ``phonenumbers`` is absent; callers fall
        back to syntax.
        """
        library = _phonenumbers()
        if library is None:
            return None

        raw = str(value or "").strip()
        if not raw:
            return None

        region = region or cls.default_region()
        parsed = cls._parse_one(raw, region)

        # `00` is the international access code across most of the world, but
        # not everywhere — Australia dials out on `0011`, so libphonenumber
        # reading an AU number will not accept `0061 4…`. People write it that
        # way all the same, so retry `00…` as `+…`.
        if parsed is None:
            digits = re.sub(r"\D", "", raw)
            if digits.startswith("00") and len(digits) > 4:
                parsed = cls._parse_one(f"+{digits[2:]}", region)

        return parsed

    @staticmethod
    def _parse_one(raw, region):
        """Parsed number when libphonenumber both reads and validates it, else None."""
        library = _phonenumbers()
        try:
            parsed = library.parse(raw, region)
        except library.NumberParseException:
            return None
        return parsed if library.is_valid_number(parsed) else None

    # -- storage ------------------------------------------------------------

    @classmethod
    def normalise(cls, value, region=None):
        """E.164 for a number from any country, or ``None`` when it is not a number.

        ``region`` is only consulted for a number written without a country code
        — ``0401773013`` is read against the default region, while
        ``+1 415 555 0132`` is American no matter what is passed here.

        Rejects anything libphonenumber cannot validate, so a note in the phone
        box or a mistyped number comes back ``None`` rather than a
        plausible-looking string that dials nowhere.
        """
        library = _phonenumbers()
        if library is None:
            _warn_fallback()
            raw = str(value or "").strip().replace(" ", "")
            return raw if _E164_SYNTAX.match(raw) else None

        parsed = cls.parse(value, region)
        if parsed is None:
            return None
        return library.format_number(parsed, library.PhoneNumberFormat.E164)

    @classmethod
    def normalise_or_original(cls, value, region=None):
        """Normalised number, or ``value`` untouched when it is not one.

        This is the form to use when writing to the database. ``normalise``
        returns ``None`` for anything it cannot validate, and a save hook must
        not turn that ``None`` into a blanked column — an extension, a second
        number in the same box, or a note someone typed is still the best record
        of what the customer gave us.
        """
        if value is None:
            return None
        return cls.normalise(value, region) or value

    # -- display ------------------------------------------------------------

    @classmethod
    def format_national(cls, value, region=None):
        """The number as a local would write it, or ``value`` trimmed if unreadable.

        ``+61400000000`` renders ``0400 000 000``. Display only — phone columns
        are often narrow and the grouping spaces overflow them.
        """
        library = _phonenumbers()
        raw = str(value or "").strip()
        if library is None:
            _warn_fallback()
            return raw
        parsed = cls.parse(value, region)
        if parsed is None:
            return raw
        return library.format_number(parsed, library.PhoneNumberFormat.NATIONAL)

    @classmethod
    def national_digits(cls, value, region=None):
        """National form with the separators removed — ``"0400000000"``.

        The digit string a local would dial, which is what a legacy phone column
        usually holds. Falls back to the raw digits when the number cannot be
        read, so a caller matching on substrings still has something to match —
        and for the same reason this is the one place the ``phonenumbers``
        fallback still returns a useful answer rather than ``None``.
        """
        raw = str(value or "").strip()
        if not raw:
            return ""
        parsed = cls.parse(raw, region)
        if parsed is None:
            return re.sub(r"\D", "", raw)
        library = _phonenumbers()
        national = library.format_number(parsed, library.PhoneNumberFormat.NATIONAL)
        return re.sub(r"\D", "", national)

    # -- validation ---------------------------------------------------------

    @classmethod
    def normalise_serviceable(cls, value, regions=None, region=None):
        """E.164 when the number is one we can take work from, else ``None``.

        ``None`` means "not a customer we can service", and the caller decides
        what to say about it — this raises nothing.

        With no regions configured any valid number passes, which is the right
        behaviour for a business that has stopped caring where the customer is.

        Returns the international form even for numbers a local would dial as-is
        (13/1300/1800 in Australia). Every phone column is stored E.164 anyway,
        so returning the national form here only meant the value changed shape
        between validation and storage.
        """
        library = _phonenumbers()
        if library is None:
            _warn_fallback()
            # Region cannot be determined without the library, so a syntactically
            # valid number is accepted rather than rejected on a guess.
            return cls.normalise(value, region)

        parsed = cls.parse(value, region)
        if parsed is None:
            return None

        if library.number_type(parsed) in cls.uncontactable_types():
            return None

        allowed = tuple(regions) if regions is not None else cls.serviceable_regions()
        if allowed and cls.region_of(parsed) not in allowed:
            return None

        return library.format_number(parsed, library.PhoneNumberFormat.E164)

    @classmethod
    def is_serviceable(cls, value, regions=None, region=None):
        """True when ``normalise_serviceable`` accepts ``value``."""
        return cls.normalise_serviceable(value, regions, region) is not None

    @classmethod
    def kind(cls, value, regions=None, region=None):
        """``MOBILE``/``LANDLINE``/``SERVICE`` for a serviceable number, else ``None``.

        Only ever answers for a number ``normalise_serviceable`` accepts, so a
        caller asking "is this a mobile" cannot be told yes about a number in a
        country we do not work in, or about a premium-rate line.

        Without ``phonenumbers`` there is no number type to read, so this is
        ``None`` for everything — a caller branching on the kind gets the
        cautious answer rather than a guess.
        """
        library = _phonenumbers()
        if library is None:
            _warn_fallback()
            return None
        parsed = cls.parse(value, region)
        if parsed is None or not cls.is_serviceable(value, regions, region):
            return None
        return cls.kind_by_type().get(library.number_type(parsed))

    @classmethod
    def is_mobile(cls, value, regions=None, region=None):
        """True when ``value`` is a mobile we could send an SMS to."""
        return cls.kind(value, regions, region) == cls.MOBILE

    @classmethod
    def region_of(cls, value, region=None):
        """ISO 3166-1 alpha-2 code for a number, or ``None`` when unreadable.

        Accepts either a raw string or an already-parsed number, so a caller that
        has parsed once does not pay for it twice.
        """
        library = _phonenumbers()
        if library is None:
            return None
        parsed = value if isinstance(value, library.PhoneNumber) else cls.parse(value, region)
        if parsed is None:
            return None
        return library.region_code_for_number(parsed)

    # -- identity -----------------------------------------------------------

    @classmethod
    def contact_key(cls, value, region=None):
        """The identity key for a phone number — digits only, country code applied.

        Two numbers belong to the same person when their keys match, whatever the
        spacing or trunk prefix: ``0412 345 678``, ``+61412345678`` and
        ``61412345678`` all key on ``61412345678``. Alphanumeric senders (OTP
        services) have no digits to key on, so their name is used instead —
        upper-cased, since providers are not consistent about case.

        Unlike ``normalise_serviceable`` this rejects nothing: a number from a
        country we do not work in keys on its own digits rather than becoming
        ``None``. Identity and callability are different questions — a number we
        cannot dial still tells us who wrote in.

        Keys get persisted by consuming projects, so the hand-rolled path below
        is kept as the fallback for anything libphonenumber will not parse. For
        every number it *does* parse the two agree, which is what lets the
        library be swapped in without orphaning a stored key.
        """
        raw = str(value or "").strip()

        e164 = cls.normalise(raw, region)
        if e164:
            return e164.lstrip("+")[:32]

        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return raw.upper()[:32]
        if raw.startswith("+"):
            # Already international: keep every digit. Truncating to a suffix
            # would let two different countries' numbers collapse into one thread.
            return digits[:32]
        if digits.startswith("0") and len(digits) > 9:
            # National format that libphonenumber rejected — an out-of-range or
            # mistyped number. Expand it against the default region so it still
            # keys consistently with itself.
            return f"{cls.country_code(region)}{digits[1:]}"[:32]
        # Anything else (already country-prefixed, or a short code) keys on digits.
        return digits[:32]

    @classmethod
    def is_same_number(cls, first, second, region=None):
        """True when two values are one number, whatever shape each is written in.

        A blank on either side never matches — an unknown number is not proof
        two numbers are the same.
        """
        first_key = cls.contact_key(first, region)
        return bool(first_key) and first_key == cls.contact_key(second, region)

    # -- search -------------------------------------------------------------

    @classmethod
    def search_variants(cls, value, region=None):
        """Digit strings a stored number could hold for this search term.

        Numbers are stored E.164, but people search using the spelling on the
        screen in front of them — ``0400`` will never be a substring of
        ``+61400000000``. This returns both readings so a plain ``icontains``
        still finds the row.

        Works on a partial term, which is the point: a search box gets ``0400``
        far more often than a whole number, and a parse-based approach cannot
        help with a fragment.
        """
        digits = re.sub(r"\D", "", str(value or ""))
        if not digits:
            return []

        code = cls.country_code(region)
        variants = {digits}
        if digits.startswith("0"):
            if code:
                variants.add(f"{code}{digits[1:]}")
        elif code and digits.startswith(code):
            rest = digits[len(code):]
            if rest:
                variants.add(f"0{rest}")
                variants.add(rest)
        return sorted(variants)

    @classmethod
    def search_q(cls, field, value, region=None):
        """A ``Q`` matching ``field`` against every spelling of ``value``.

        ``None`` when the term holds no digits, so a caller can skip the phone
        clause rather than OR-ing in something that matches everything.
        """
        from django.db.models import Q

        variants = cls.search_variants(value, region)
        if not variants:
            return None

        clause = Q()
        for variant in variants:
            clause |= Q(**{f"{field}__icontains": variant})
        return clause
