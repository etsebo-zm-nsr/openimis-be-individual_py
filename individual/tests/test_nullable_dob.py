import json
import uuid
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from unittest import skipIf

from core.test_helpers import LogInHelper, create_test_interactive_user
from individual.models import (
    Individual,
    IndividualDataSource,
    IndividualDataSourceUpload,
)
from individual.services import IndividualImportService, IndividualService
from individual.tests.test_helpers import create_individual, create_test_village
from individual.workflows.base_individual_upload import process_import_individuals_workflow
from individual.workflows.individual_update_valid import process_update_valid_individuals_workflow
from individual.workflows.individual_upload_valid import process_import_valid_individuals_workflow
from individual.workflows.utils import PythonWorkflowHandlerException, SqlProcedurePythonWorkflow
from opensearch_reports.service import BaseSyncDocument


class NullableDobServiceTest(TestCase):
    """The registry stores what a source asserted, so a missing date of birth is NULL, never an invented date."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()
        cls.service = IndividualService(cls.user)

    def test_create_without_dob_saves_null(self):
        result = self.service.create({'first_name': 'NoDob', 'last_name': 'Person'})
        self.assertTrue(result.get('success', False), result.get('detail', "No details provided"))
        individual = Individual.objects.get(id=result['data']['uuid'])
        self.assertIsNone(individual.dob)

    def test_create_with_explicit_null_dob_saves_null(self):
        result = self.service.create({'first_name': 'NullDob', 'last_name': 'Person', 'dob': None})
        self.assertTrue(result.get('success', False), result.get('detail', "No details provided"))
        self.assertIsNone(Individual.objects.get(id=result['data']['uuid']).dob)

    def test_update_can_leave_dob_unset(self):
        created = self.service.create({'first_name': 'Before', 'last_name': 'Person'})
        individual_id = created['data']['uuid']
        result = self.service.update({'id': individual_id, 'first_name': 'After', 'last_name': 'Person'})
        self.assertTrue(result.get('success', False), result.get('detail', "No details provided"))
        individual = Individual.objects.get(id=individual_id)
        self.assertEqual(individual.first_name, 'After')
        self.assertIsNone(individual.dob)

    def test_history_row_records_null_dob(self):
        individual = Individual(first_name='Hist', last_name='Person')
        individual.save(user=self.user)
        self.assertIsNone(Individual.history.filter(id=individual.id).first().dob)


@skipIf(
    connection.vendor != "postgresql",
    "Skipping tests due to implementation usage of validate_json_schema, which is a postgres specific extension."
)
class NullableDobCsvImportTest(TestCase):
    """A CSV import must succeed for rows whose dob is blank, and for files with no dob column at all."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.doc_update_patcher = patch.object(BaseSyncDocument, "update")
        cls.doc_update_patcher.start()
        cls.schema_patcher = patch("individual.apps.IndividualConfig.individual_schema", "{}")
        cls.schema_patcher.start()
        cls.maker_checker_patcher = patch(
            "individual.apps.IndividualConfig.enable_maker_checker_for_individual_upload", False)
        cls.maker_checker_patcher.start()
        cls.group_maker_checker_patcher = patch(
            "individual.apps.IndividualConfig.enable_maker_checker_for_group_upload", False)
        cls.group_maker_checker_patcher.start()

    @classmethod
    def tearDownClass(cls):
        patch.stopall()
        super().tearDownClass()

    def setUp(self):
        self.user = create_test_interactive_user(username='DobImporter')
        self.village = create_test_village({'name': 'Nullville', 'code': 'NvL'})

    def _import(self, csv_text):
        """Drive the real upload path: CSV -> data sources -> the SQL import workflow."""
        workflow = MagicMock()
        workflow.name = 'Test Workflow'
        workflow.run.side_effect = lambda args: process_import_individuals_workflow(
            args['user_uuid'], args['upload_uuid'])
        csv_file = SimpleUploadedFile('dob.csv', csv_text.encode('utf-8'), content_type='text/csv')
        result = IndividualImportService(self.user).import_individuals(csv_file, workflow, None)
        workflow.run.assert_called_once()
        return IndividualDataSourceUpload.objects.get(uuid=result['data']['upload_uuid'])

    def _individual(self, upload, first_name):
        source = IndividualDataSource.objects.get(upload=upload, json_ext__first_name=first_name)
        self.assertIsNotNone(source.individual_id, f"{first_name} was not imported")
        return Individual.objects.get(id=source.individual_id)

    def test_blank_dob_cell_imports_with_null_dob(self):
        upload = self._import(
            "first_name,last_name,dob,location_name,location_code\n"
            f"Dated,Person,1985-03-04,{self.village.name},{self.village.code}\n"
            f"Blank,Person,,{self.village.name},{self.village.code}\n"
            f"Spaces,Person,   ,{self.village.name},{self.village.code}\n"
        )
        self.assertEqual(upload.status, IndividualDataSourceUpload.Status.SUCCESS, upload.error)
        self.assertEqual(self._individual(upload, 'Dated').dob.isoformat(), '1985-03-04')
        self.assertIsNone(self._individual(upload, 'Blank').dob)
        self.assertIsNone(self._individual(upload, 'Spaces').dob)

    def test_csv_without_dob_column_imports_with_null_dob(self):
        upload = self._import(
            "first_name,last_name,location_name,location_code\n"
            f"Nocol,Person,{self.village.name},{self.village.code}\n"
        )
        self.assertEqual(upload.status, IndividualDataSourceUpload.Status.SUCCESS, upload.error)
        self.assertIsNone(self._individual(upload, 'Nocol').dob)
        self.assertNotIn('dob', json.loads(json.dumps(
            IndividualDataSource.objects.get(upload=upload).json_ext)))

    def test_accepted_subset_import_without_dob(self):
        """The checker-approved path (a list of accepted rows) runs its own copy of the import SQL."""
        upload = IndividualDataSourceUpload(source_name='csv', source_type='upload', status="PENDING")
        upload.save(user=self.user)
        sources = []
        for first_name, extra in (('PartialMissing', {}), ('PartialBlank', {'dob': ''})):
            source = IndividualDataSource(
                upload=upload,
                json_ext={'first_name': first_name, 'last_name': 'Person', 'location_name': self.village.name,
                          'location_code': self.village.code, **extra},
                validations={'validation_errors': []},
            )
            source.save(user=self.user)
            sources.append(source)

        process_import_valid_individuals_workflow(
            str(self.user.id), str(upload.uuid), [str(source.id) for source in sources])

        upload.refresh_from_db()
        # the partial SQL leaves the final status to the task executor; it only flags failures
        self.assertNotEqual(upload.status, IndividualDataSourceUpload.Status.FAIL, upload.error)
        for first_name in ('PartialMissing', 'PartialBlank'):
            self.assertIsNone(self._individual(upload, first_name).dob)


@skipIf(
    connection.vendor != "postgresql",
    "Skipping tests due to implementation usage of validate_json_schema, which is a postgres specific extension."
)
class NullableDobUpdateImportTest(TestCase):
    """A blank dob cell in an update file means "no value supplied": it must not error or wipe the stored dob."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patch.object(BaseSyncDocument, "update").start()
        patch("individual.apps.IndividualConfig.individual_schema", "{}").start()

    @classmethod
    def tearDownClass(cls):
        patch.stopall()
        super().tearDownClass()

    def _run(self, accepted):
        user = create_test_interactive_user(username='DobUpdater')
        village = create_test_village({'name': 'Updateville', 'code': 'UpV'})
        individual = create_individual(user.username, {'first_name': 'Old', 'last_name': 'Name', 'dob': '1990-05-06'})
        upload = IndividualDataSourceUpload(source_name='csv', source_type='update', status="PENDING")
        upload.save(user=user)
        source = IndividualDataSource(
            upload=upload,
            json_ext={'ID': str(individual.id), 'first_name': 'New', 'last_name': 'Name', 'dob': '',
                      'location_name': village.name, 'location_code': village.code},
            validations={'validation_errors': []},
        )
        source.save(user=user)

        process_update_valid_individuals_workflow(
            str(user.id), str(upload.uuid), [str(source.id)] if accepted else None)

        upload.refresh_from_db()
        self.assertNotEqual(upload.status, IndividualDataSourceUpload.Status.FAIL, upload.error)
        individual.refresh_from_db()
        self.assertEqual(individual.first_name, 'New')
        self.assertEqual(individual.dob.isoformat(), '1990-05-06')

    def test_blank_dob_keeps_stored_dob(self):
        self._run(accepted=False)

    def test_blank_dob_keeps_stored_dob_for_accepted_subset(self):
        self._run(accepted=True)


class NullableDobHeaderTest(TestCase):
    """dob is no longer a required CSV header, but it remains an accepted one."""

    def setUp(self):
        user = create_test_interactive_user(username='DobHeaders')
        patch('individual.workflows.utils.load_dataframe', return_value=pd.DataFrame()).start()
        patch('individual.workflows.utils.IndividualConfig.individual_schema', json.dumps({"properties": {}})).start()
        patch.object(BaseSyncDocument, "update").start()
        self.executor = SqlProcedurePythonWorkflow(uuid.uuid4(), user.id)

    def tearDown(self):
        patch.stopall()
        super().tearDown()

    def test_headers_without_dob_are_valid(self):
        self.executor.df = pd.DataFrame(
            columns=['first_name', 'last_name', 'id', 'location_name', 'location_code'])
        self.executor.validate_dataframe_headers()

    def test_headers_with_dob_are_valid(self):
        self.executor.df = pd.DataFrame(
            columns=['first_name', 'last_name', 'dob', 'id', 'location_name', 'location_code'])
        self.executor.validate_dataframe_headers()

    def test_other_required_headers_are_still_enforced(self):
        self.executor.df = pd.DataFrame(columns=['last_name', 'id', 'location_name', 'location_code'])
        with self.assertRaises(PythonWorkflowHandlerException) as cm:
            self.executor.validate_dataframe_headers()
        self.assertIn("missing essential header: first_name", str(cm.exception))
