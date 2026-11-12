import os
from unittest.mock import patch, MagicMock
from rest_framework.test import APITestCase
from rest_framework import status
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone
from core.test_helpers import create_test_interactive_user
from core.models import ModuleConfiguration
from individual.apps import IndividualConfig
from individual.tests.test_helpers import reload_individual_config


class TestView(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_file_path = os.path.join(
            os.path.dirname(__file__), 'fixtures', 'individual_upload.csv'
        )
        cls.test_config_path = os.path.join(
            os.path.dirname(__file__), 'fixtures', 'individual_config.json'
        )

    def setUp(self):
        self.download_url = reverse('download_template_file')
        self.upload_url = reverse('import_individuals')
        self.upload_data = {
            'workflow_name': 'Test Workflow',
            'workflow_group': 'Test Group',
            'group_aggregation_column': 'group_code'
        }
        self.admin_user = create_test_interactive_user()
        self.client.force_authenticate(user=self.admin_user)

    def test_download_template_file(self):
        response = self.client.get(self.download_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(
            'attachment; filename="individual_upload_template.csv"',
            response['Content-Disposition']
        )

        expected_base_csv_header = 'first_name,last_name,location_name,location_code,id'
        content = b"".join(response.streaming_content).decode('utf-8')
        # dob is optional but still offered as a template column
        self.assertIn('dob', content.split('\n')[0].split(','))
        self.assertTrue(
            expected_base_csv_header in content,
            f'Expect csv template header to contain {expected_base_csv_header}, but got {content}'
        )

    def test_download_template_file_on_individual_schema_update(self):
        # First set the individual config to be empty
        config = ModuleConfiguration.objects.filter(module='individual', layer='be').first()
        self.addCleanup(reload_individual_config, config.config if config else '{}')
        if config is None:
            config = ModuleConfiguration(module='individual', layer='be', config='{}')
        else:
            config.config = '{}'
        # The module reload is queued with transaction.on_commit, which never
        # runs inside a TestCase's rolled-back transaction.
        with self.captureOnCommitCallbacks(execute=True):
            config.save()

        # Then update individual config to with the fixture config
        with open(self.test_config_path, 'rb') as test_file:
            config.config = test_file.read()
        with self.captureOnCommitCallbacks(execute=True):
            config.save()

        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Check the template file contains individual schema fields from fixture config
        content = b"".join(response.streaming_content).decode('utf-8')
        for expected_column in ('poor', 'educated_level', 'number_of_children'):
            self.assertTrue(
                expected_column in content,
                f'Expect csv template header to contain {expected_column}, but got {content}'
            )

    @patch('individual.views.WorkflowService')
    @patch('individual.views.IndividualImportService')
    @patch('individual.views.DefaultStorageFileHandler')
    def test_import_individuals_success(
            self, mock_file_handler, mock_individual_import_service, mock_workflow_service):

        mock_workflow_service.get_workflows.return_value = {
            'success': True,
            'data': {
                'workflows': [{'id': 1, 'name': 'Test Workflow'}]
            }
        }

        mock_individual_import_service.return_value.import_individuals.return_value = {
            'success': True,
            'message': 'Import successful',
            'details': None
        }

        mock_handler_instance = MagicMock()
        mock_file_handler.return_value = mock_handler_instance

        with open(self.test_file_path, 'rb') as test_file:
            response = self.client.post(
                self.upload_url,
                data={**self.upload_data, 'file': test_file},
                format='multipart'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        self.assertIn('message', response.data)

        mock_handler_instance.save_file.assert_called_once()

    @patch('individual.views.WorkflowService.get_workflows')
    @patch('individual.views.IndividualImportService.import_individuals')
    def test_import_individuals_upload_same_file_twice_triggers_rename(
        self, mock_import_service, mock_get_workflows
    ):
        mock_import_service.return_value = {'success': True}
        mock_get_workflows.return_value = {
            'success': True,
            'data': {
                'workflows': [{'id': 1, 'name': 'Test Workflow'}]
            }
        }

        test_file_name = "test_individuals.csv"
        dir_path = os.path.dirname(IndividualConfig.get_individual_upload_file_path(test_file_name))
        if default_storage.exists(dir_path):
            for filename in default_storage.listdir(dir_path)[1]:  # files only
                path = os.path.join(dir_path, filename)
                if default_storage.exists(path):
                    default_storage.delete(path)

        with open(self.test_file_path, 'rb') as test_file:
            upload_content = test_file.read()

        # First upload, freeze time at T0
        with patch("individual.views.timezone.now") as mock_now:
            mock_now.return_value = timezone.datetime(2025, 9, 11, 12, 0, 0)
            first_upload = SimpleUploadedFile(
                test_file_name,
                upload_content,
                content_type='text/csv'
            )

            response1 = self.client.post(
                self.upload_url,
                data={**self.upload_data, 'file': first_upload},
                format='multipart'
            )
            self.assertEqual(response1.status_code, status.HTTP_200_OK)

        first_path = IndividualConfig.get_individual_upload_file_path(test_file_name)
        self.assertTrue(default_storage.exists(first_path))

        # Second upload, tick time forward by +1 second (same filename, should be renamed automatically)
        with patch("individual.views.timezone.now") as mock_now:
            mock_now.return_value = timezone.datetime(2025, 9, 11, 12, 0, 1)
            second_upload = SimpleUploadedFile(
                name=test_file_name,
                content=upload_content,
                content_type='text/csv'
            )

            response2 = self.client.post(
                self.upload_url,
                data={**self.upload_data, 'file': second_upload},
                format='multipart'
            )
            self.assertEqual(response2.status_code, status.HTTP_200_OK)

        # Check that the renamed file exists and matches the expected suffix pattern
        renamed_files = [
            f for f in default_storage.listdir(os.path.dirname(
                IndividualConfig.get_individual_upload_file_path(test_file_name)))[1]
            if f.startswith("test_individuals_") and f.endswith(".csv")
        ]
        self.assertEqual(len(renamed_files), 1)
        self.assertRegex(renamed_files[0], r"^test_individuals_\d{14}\.csv$")

    @patch('individual.views.WorkflowService')
    @patch('individual.views.IndividualImportService')
    @patch('individual.views.DefaultStorageFileHandler')
    def test_import_individuals_import_service_failure(
            self, mock_file_handler, mock_individual_import_service, mock_workflow_service):

        mock_handler_instance = MagicMock()
        mock_file_handler.return_value = mock_handler_instance

        mock_individual_import_service.return_value.import_individuals.return_value = {
            'success': False,
            'message': 'Import service not available',
            'details': 'Dummy import service issue'
        }

        mock_workflow_service.get_workflows.return_value = {
            'success': True,
            'data': {
                'workflows': [{'id': 1, 'name': 'Test Workflow'}]
            }
        }

        with open(self.test_file_path, 'rb') as test_file:
            response = self.client.post(
                self.upload_url,
                data={**self.upload_data, 'file': test_file},
                format='multipart'
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

        mock_handler_instance.save_file.assert_called_once()
        mock_handler_instance.remove_file.assert_called_once()

    @patch('individual.views.WorkflowService')
    @patch('individual.views.DefaultStorageFileHandler')
    def test_import_individuals_workflow_service_failure(
            self, mock_file_handler, mock_workflow_service):

        mock_handler_instance = MagicMock()
        mock_file_handler.return_value = mock_handler_instance

        mock_workflow_service.get_workflows.return_value = {
            'success': False,
            'message': 'Workflow service not available',
            'details': 'Dummy workflow service issue'
        }

        with open(self.test_file_path, 'rb') as test_file:
            response = self.client.post(
                self.upload_url,
                data={**self.upload_data, 'file': test_file},
                format='multipart'
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

        mock_handler_instance.save_file.assert_not_called()
        mock_handler_instance.remove_file.assert_not_called()
