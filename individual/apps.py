import copy
import json
import logging

from django.apps import AppConfig

from core.custom_filters import CustomFilterRegistryPoint
from core.data_masking import MaskingClassRegistryPoint
from core.module_config_registry import register_validator, register_reloader
from core.rights_declaration import RightsDeclaration

logger = logging.getLogger(__name__)

MODULE_NAME = "individual"

# Rights, by entity then by action. Two distinct entities, and two distinct blocks of
# identifiers in the openIMIS catalogue: `individual` (159xxx) is the register of
# people, `group` (180xxx) that of households. No identifier is shared between the two.
#
# The django names all carry the `individual` app_label: `Group` is a model of this
# app, not of a "group" app - the entity is named after the business object, the django
# name after the app that hosts the model.
DJANGO_PERMS = {
    "individual": {
        "query": ("individual.view_individual", 159001),
        "create": ("individual.add_individual", 159002),
        "update": ("individual.change_individual", 159003),
        "delete": ("individual.delete_individual", 159004),
        # A business action: restoring a soft-deleted person is neither a creation
        # nor an ordinary modification, and already carries its own identifier.
        "undoDelete": ("individual.undo_delete_individual", 159005),
    },
    "group": {
        "query": ("individual.view_group", 180001),
        "create": ("individual.add_group", 180002),
        "update": ("individual.change_group", 180003),
        "delete": ("individual.delete_group", 180004),
    },
}

_PERM_CFG = {
    "gql_individual_search_perms": ("individual", "query"),
    "gql_individual_create_perms": ("individual", "create"),
    "gql_individual_update_perms": ("individual", "update"),
    "gql_individual_delete_perms": ("individual", "delete"),
    "gql_individual_undo_delete_perms": ("individual", "undoDelete"),
    "gql_group_search_perms": ("group", "query"),
    "gql_group_create_perms": ("group", "create"),
    "gql_group_update_perms": ("group", "update"),
    "gql_group_delete_perms": ("group", "delete"),
}

RIGHTS = RightsDeclaration(MODULE_NAME, DJANGO_PERMS, _PERM_CFG)

perms = RIGHTS.perms
django_perms = RIGHTS.django_perm_names
configured_perms = RIGHTS.configured
require = RIGHTS.require


DEFAULT_CONFIG = {
    "check_individual_update": True,
    "check_individual_delete": True,
    "check_group_individual_update": True,
    "check_group_create": True,
    "check_group_delete": True,
    "individual_schema": json.dumps({
        "properties": {
            "email": {"type": "string"},
            "able_bodied": {"type": "boolean"},
            "national_id": {"type": "string"},
            "educated_level": {"type": "string"},
            "chronic_illness": {"type": "boolean"},
            "national_id_type": {"type": "string"},
            "number_of_elderly": {"type": "integer"},
            "number_of_children": {"type": "integer"},
            "beneficiary_data_source": {"type": "string"}
        }
    }),
    "individual_accept_enrolment": "individual_service.create_accept_enrolment_task",
    "validation_import_valid_items_workflow": "individual-import-valid-items",
    "validation_calculation_uuid": "4362f958-5894-435b-9bda-df6cadf88352",
    "validation_import_valid_items": "individual_validation.import_valid_items",
    "validation_import_group_valid_items": "individual_validation.import_group_valid_items",
    "validation_upload_valid_items": "individual_validation.upload_valid_items",
    "validation_upload_valid_items_workflow": "individual-upload-valid-items.individual-upload-valid-items",
    "enable_python_workflows": True,
    "enable_maker_checker_logic_import": True,
    "enable_maker_checker_for_individual_upload": True,
    "enable_maker_checker_for_group_upload": True,
    "enable_maker_checker_for_individual_update": True,
    "enable_maker_checker_for_group_update": True,
    "individual_masking_enabled": True,
    "individual_mask_fields": [
        'json_ext.beneficiary_data_source',
        'json_ext.educated_level'
    ],
    "individual_base_fields": [
        'first_name', 'last_name', 'location_name', 'location_code', 'id'
    ]
}


class IndividualConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

    # Rights: constants, no longer overridable. They go neither through DEFAULT_CFG
    # nor through ready(): `ModuleConfiguration.get_or_default` now ignores any
    # `_perms` key stored in the database.
    gql_individual_search_perms = RIGHTS.perms("individual", "query")
    gql_individual_create_perms = RIGHTS.perms("individual", "create")
    gql_individual_update_perms = RIGHTS.perms("individual", "update")
    gql_individual_delete_perms = RIGHTS.perms("individual", "delete")
    gql_individual_undo_delete_perms = RIGHTS.perms("individual", "undoDelete")

    gql_group_search_perms = RIGHTS.perms("group", "query")
    gql_group_create_perms = RIGHTS.perms("group", "create")
    gql_group_update_perms = RIGHTS.perms("group", "update")
    gql_group_delete_perms = RIGHTS.perms("group", "delete")
    check_individual_update = None
    check_individual_delete = None
    check_group_individual_update = None
    check_group_create = None
    check_group_delete = None
    python_individual_import_workflow_group = None
    python_individual_import_workflow_name = None
    individual_schema = None
    individual_accept_enrolment = None
    validation_calculation_uuid = None
    validation_import_valid_items_workflow = None
    validation_import_valid_items = None
    validation_import_group_valid_items = None

    enable_python_workflows = None
    enable_maker_checker_logic_import = None

    validation_upload_valid_items_workflow = None
    validation_upload_valid_items = None

    enable_maker_checker_for_individual_upload = None
    enable_maker_checker_for_group_upload = None
    enable_maker_checker_for_individual_update = None
    enable_maker_checker_for_group_update = None
    individual_mask_fields = None
    individual_masking_enabled = None
    individual_base_fields = None

    def ready(self):
        from core.models import ModuleConfiguration

        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self.__load_config(cfg)
        self.__validate_individual_schema(cfg)
        self.__initialize_custom_filters()
        self._set_up_workflows()
        self.__register_masking_class()
        register_validator(self.name, self._validate_module_config)
        register_reloader(self.name, self._reload_module_config)

    def _merge_with_defaults(self, instance):
        # `instance._cfg` has already stripped the `_perms` keys from the stored
        # config, and DEFAULT_CONFIG holds none any more: the merge therefore cannot
        # reintroduce a right, and `__load_config` leaves the class constants intact.
        return {**copy.deepcopy(DEFAULT_CONFIG), **instance._cfg}

    def _validate_module_config(self, instance):
        cfg = self._merge_with_defaults(instance)
        self.__validate_individual_schema(cfg)

    def _reload_module_config(self, instance):
        cfg = self._merge_with_defaults(instance)
        self.__load_config(cfg)

        # Reinitialize custom filters to apply the new schema
        self.__initialize_custom_filters()

        # Workflow needs to be re-registered, otherwise default/invalid ones would apply
        self._set_up_workflows()

        # TODO: handle reloading of masking configs
        logger.info(f"Reloaded app configs (except masking configs) for {self.name} module")

    @classmethod
    def __load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(IndividualConfig, field):
                setattr(IndividualConfig, field, cfg[field])

    @classmethod
    def __validate_individual_schema(cls, cfg):
        # TODO: validate against cls.individual_schema it is already assigned
        if 'individual_schema' not in cfg:
            logging.error('No individual_schema in individual module config.')
            return

        from core.utils import validate_json_schema
        errors = validate_json_schema(cfg['individual_schema'])

        if errors:
            error_messages = [error['message'] for error in errors]
            logging.error('Schema validation errors in individual schema: %s', ', '.join(error_messages))

    @classmethod
    def __initialize_custom_filters(cls):
        from individual.custom_filters import (
            IndividualCustomFilterWizard,
            GroupCustomFilterWizard,
            GroupIndividualCustomFilterWizard,
        )
        CustomFilterRegistryPoint.register_custom_filters(
            module_name=cls.name,
            custom_filter_class_list=[
                IndividualCustomFilterWizard,
                GroupCustomFilterWizard,
                GroupIndividualCustomFilterWizard
            ]
        )

    def __register_masking_class(cls):
        from individual.data_masking import IndividualMask, IndividualHistoryMask
        MaskingClassRegistryPoint.register_masking_class(
            masking_class_list=[IndividualMask(), IndividualHistoryMask()]
        )

    def _set_up_workflows(self):
        from workflow.systems.python import PythonWorkflowAdaptor
        from individual.workflows import process_import_individuals_workflow, \
            process_update_valid_individuals_workflow, \
            process_import_valid_individuals_workflow, \
            process_update_individuals_workflow

        if self.enable_python_workflows:
            PythonWorkflowAdaptor.register_workflow(
                'Python Import Individuals',
                'individual',
                process_import_individuals_workflow
            )
            PythonWorkflowAdaptor.register_workflow(
                'Python Update Individuals',
                'individual',
                process_update_individuals_workflow
            )
            PythonWorkflowAdaptor.register_workflow(
                'Python Valid Upload Individuals',
                'individual',
                process_import_valid_individuals_workflow
            )
            PythonWorkflowAdaptor.register_workflow(
                'Python Valid Update Individuals',
                'individual',
                process_update_valid_individuals_workflow
            )

            # Replace default setup for invalid workflow to be python one
            if IndividualConfig.enable_python_workflows is True:

                # Resolve Maker-Checker Workflows Overwrite
                if self.validation_import_valid_items_workflow == DEFAULT_CONFIG[
                        'validation_import_valid_items_workflow']:
                    IndividualConfig.validation_import_valid_items_workflow \
                        = 'individual.Python Valid Upload Individuals'

                if self.validation_upload_valid_items_workflow == DEFAULT_CONFIG[
                        'validation_upload_valid_items_workflow']:
                    IndividualConfig.validation_upload_valid_items_workflow \
                        = 'individual.Python Valid Update Individuals'

    @staticmethod
    def get_individual_upload_file_path(filename):
        if filename:
            return f"individual_upload/{filename}"
        return "individual_upload"
