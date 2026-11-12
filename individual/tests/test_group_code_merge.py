from django.test import TestCase

from core.test_helpers import LogInHelper
from individual.models import (
    Group, GroupIndividual, Individual, IndividualDataSource, IndividualDataSourceUpload,
    IndividualDataUploadRecords,
)
from individual.signals.on_validation_import_valid_items import (
    BaseGroupColumnAggregationClass, IndividualItemsImportTaskCompletionEvent,
)


class GroupCodeMergeAcrossUploadsTest(TestCase):
    """A household whose members arrive in more than one upload must still be assembled.

    When an upload names a group_code that already exists, the group is rebuilt from all
    of its members - the new ones and those already assigned. The existing members had
    `individual_role` stripped from their json_ext by `_clean_json_ext()` when their own
    upload finished, so `_individual_role_parser` received None and `None.upper()` failed
    the group step for the whole upload. The individuals were already created, so they
    were left in no household at all.

    Any import split into batches hits this whenever a household straddles two of them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()

    def _upload(self, name):
        upload = IndividualDataSourceUpload(source_name=name, source_type="test")
        upload.save(user=self.user)
        record = IndividualDataUploadRecords(
            data_upload=upload, workflow="test", json_ext={"group_aggregation_column": "group_code"})
        record.save(user=self.user)
        return upload, record

    def _stage(self, upload, first_name, json_ext):
        individual = Individual(first_name=first_name, last_name="Test", dob="1990-01-01",
                                json_ext=dict(json_ext))
        individual.save(user=self.user)
        IndividualDataSource(upload=upload, individual=individual,
                             json_ext=dict(json_ext)).save(user=self.user)
        return individual

    def _assemble_groups(self, upload, record):
        """The group step of an import with maker-checker disabled."""
        event = IndividualItemsImportTaskCompletionEvent("test.test", record, str(upload.id), self.user)
        event.set_group_aggregation_column(event.group_code_str)
        event.individuals = event._query_individuals()
        event.grouped_individuals = event._get_grouped_individuals()
        event._create_or_update_groups_using_group_code()
        event._clean_json_ext()
        event.group_data_sources_into_entities(upload.id, self.user)

    def _members(self, code):
        return {gi.individual.first_name: (gi.role, gi.recipient_type)
                for gi in GroupIndividual.objects.filter(group__code=code, is_deleted=False)}

    def test_second_upload_adds_member_to_existing_household(self):
        code = "MERGE-TEST-1"
        first, first_record = self._upload("first")
        parent = self._stage(first, "Parent",
                             {"group_code": code, "individual_role": "HEAD", "recipient_info": 1})
        self._assemble_groups(first, first_record)

        parent.refresh_from_db()
        self.assertNotIn("individual_role", parent.json_ext,
                         "precondition: the first upload strips the role it consumed")

        second, second_record = self._upload("second")
        self._stage(second, "Child", {"group_code": code, "individual_role": "SON"})
        self._assemble_groups(second, second_record)

        self.assertEqual(Group.objects.filter(code=code, is_deleted=False).count(), 1)
        self.assertEqual(self._members(code), {
            "Parent": (GroupIndividual.Role.HEAD, GroupIndividual.RecipientType.PRIMARY),
            "Child": (GroupIndividual.Role.SON, None),
        })

    def test_role_parser_tolerates_a_missing_role(self):
        parse = BaseGroupColumnAggregationClass._individual_role_parser
        self.assertIsNone(parse(None))
        self.assertIsNone(parse(""))
        self.assertEqual(parse("son"), GroupIndividual.Role.SON)
