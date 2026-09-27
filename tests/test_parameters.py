"""System parameters and the cache in front of them (PRD §7)."""

from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from django_common_kit.models import ParameterModel
from django_common_kit.parameters import ParameterCache, get_parameter


def seed():
    return [
        {"key": "MAX_JOBS", "parameter_type": "integer", "value": 12, "is_system": True},
        {"key": "GREETING", "parameter_type": "text", "value": "hi", "is_system": True},
        {"key": "FLAGS", "parameter_type": "json", "value": {"a": 1}, "is_system": True},
    ]


class ParameterModelTests(TestCase):
    def test_value_reads_the_column_its_type_names(self):
        row = ParameterModel(key="k", parameter_type="integer")
        row.set_value("42")
        self.assertEqual(row.value_integer, 42)
        self.assertEqual(row.value, 42)

    def test_set_value_clears_the_other_columns(self):
        row = ParameterModel(key="k", parameter_type="text", value_integer=5)
        row.set_value("x")
        self.assertIsNone(row.value_integer)
        self.assertEqual(row.value, "x")


class ParameterCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_load_then_read_from_cache_not_the_database(self):
        ParameterModel.objects.create(key="A", parameter_type="text", value_text="1", is_system=True)
        ParameterCache.load_parameters_to_cache()
        # A queryset update() fires no signal, so the cache is not invalidated
        # and the stale value proves the read came from the cache.
        ParameterModel.objects.filter(key="A").update(value_text="2")
        self.assertEqual(get_parameter("A"), "1")

    def test_miss_falls_back_to_the_database_then_default(self):
        ParameterModel.objects.create(key="B", parameter_type="integer", value_integer=7, is_system=True)
        self.assertEqual(get_parameter("B"), 7)
        self.assertEqual(get_parameter("MISSING", "dflt"), "dflt")

    def test_save_invalidates(self):
        row = ParameterModel.objects.create(key="C", parameter_type="text", value_text="old", is_system=True)
        ParameterCache.load_parameters_to_cache()
        row.value_text = "new"
        row.save()
        self.assertEqual(get_parameter("C"), "new")

    def test_non_system_rows_are_not_served(self):
        ParameterModel.objects.create(key="D", parameter_type="text", value_text="x", is_system=False)
        self.assertIsNone(get_parameter("D"))

    def test_version_actually_increments(self):
        """A version that never increments is a cache that never invalidates."""
        ParameterCache.invalidate_cache()
        first = ParameterCache.get_cache_info()["version"]
        ParameterCache.invalidate_cache()
        self.assertEqual(ParameterCache.get_cache_info()["version"], first + 1)

    def test_typed_accessors_never_raise(self):
        ParameterModel.objects.create(key="E", parameter_type="text", value_text="5 ", is_system=True)
        ParameterModel.objects.create(key="F", parameter_type="text", value_text="yes", is_system=True)
        self.assertEqual(ParameterCache.get_int("E"), 5)
        self.assertEqual(ParameterCache.get_int("nope", 3), 3)
        self.assertTrue(ParameterCache.get_bool("F"))
        self.assertEqual(ParameterCache.get_str("nope", "z"), "z")


class SeedCommandTests(TestCase):
    def test_refuses_without_a_catalogue(self):
        with self.assertRaises(CommandError):
            call_command("seed_parameters")

    @override_settings(DJANGO_COMMON_KIT={"PARAMETERS": {"SEED_CATALOG": "tests.test_parameters.seed"}})
    def test_creates_then_leaves_existing_rows_alone(self):
        out = StringIO()
        call_command("seed_parameters", stdout=out)
        self.assertEqual(ParameterModel.objects.count(), 3)
        self.assertEqual(ParameterModel.objects.get(key="FLAGS").value, {"a": 1})

        ParameterModel.objects.filter(key="MAX_JOBS").update(value_integer=99)
        call_command("seed_parameters", stdout=out)
        self.assertEqual(ParameterModel.objects.get(key="MAX_JOBS").value, 99)

        call_command("seed_parameters", "--update", stdout=out)
        self.assertEqual(ParameterModel.objects.get(key="MAX_JOBS").value, 12)
