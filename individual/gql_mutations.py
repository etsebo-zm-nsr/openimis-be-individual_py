import graphene
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from core.gql.gql_mutations.base_mutation import BaseHistoryModelDeleteMutationMixin, BaseMutation, \
    BaseHistoryModelUpdateMutationMixin, BaseHistoryModelCreateMutationMixin
from core.schema import OpenIMISMutation
from individual.apps import IndividualConfig
from individual.models import Individual, Group, GroupIndividual
from individual.services import IndividualService, GroupService, GroupIndividualService, \
    CreateGroupAndMoveIndividualService
from location.models import Location, LocationManager


class CreateIndividualInputType(OpenIMISMutation.Input):
    first_name = graphene.String(required=True, max_length=255)
    last_name = graphene.String(required=True, max_length=255)
    dob = graphene.Date(required=False)
    json_ext = graphene.types.json.JSONString(required=False)
    location_id = graphene.Int(required=False)


class UpdateIndividualInputType(CreateIndividualInputType):
    id = graphene.UUID(required=True)


RoleEnum = graphene.Enum.from_enum(GroupIndividual.Role)
RecipientTypeEnum = graphene.Enum.from_enum(GroupIndividual.RecipientType)


class CreateGroupIndividualInputType(OpenIMISMutation.Input):
    group_id = graphene.UUID(required=False)
    individual_id = graphene.UUID(required=True)
    role = graphene.Field(RoleEnum, required=False)
    recipient_type = graphene.Field(RecipientTypeEnum, required=False)

    def resolve_role(self, info):
        return self.role

    def resolve_recipient_type(self, info):
        return self.recipient_type


class CreateGroupIndividualInputTypeInputObjectType(graphene.InputObjectType):
    group_id = graphene.UUID(required=False)
    individual_id = graphene.UUID(required=True)
    role = graphene.Field(RoleEnum, required=False)
    recipient_type = graphene.Field(RecipientTypeEnum, required=False)

    def resolve_role(self, info):
        return self.role

    def resolve_recipient_type(self, info):
        return self.recipient_type


class CreateGroupInputType(OpenIMISMutation.Input):
    code = graphene.String(required=True)
    individuals_data = graphene.List(CreateGroupIndividualInputTypeInputObjectType, required=False)
    location_id = graphene.Int(required=False)


class UpdateGroupInputType(OpenIMISMutation.Input):
    id = graphene.UUID(required=True)
    code = graphene.String(required=False)
    individuals_data = graphene.List(CreateGroupIndividualInputTypeInputObjectType, required=False)
    location_id = graphene.Int(required=False)


class UpdateGroupIndividualInputType(CreateGroupIndividualInputType):
    id = graphene.UUID(required=True)


class ConfirmIndividualEnrollmentInputType(OpenIMISMutation.Input):
    custom_filters = graphene.List(required=False, of_type=graphene.String)
    benefit_plan_id = graphene.String(required=True, max_lenght=255)
    status = graphene.String(required=True, max_lenght=255)


class CreateIndividualMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateIndividualMutation"
    _mutation_module = "individual"
    _model = Individual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_individual_create_perms):
            raise PermissionDenied(_("unauthorized"))
        if (
            'location_id' in data and not LocationManager().is_allowed(
                user,
                [data['location_id']]
            )
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = IndividualService(user)
        result = service.create(data)
        return result if not result['success'] else None

    class Input(CreateIndividualInputType):
        pass


class UpdateIndividualMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdateIndividualMutation"
    _mutation_module = "individual"
    _model = Individual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_individual_update_perms):
            raise PermissionDenied(_("unauthorized"))

        location_from = Individual.objects.get(id=data['id']).location_id

        location_to_check = [data['location_id']] if 'location_id' in data else []
        if location_from:
            location_to_check.append(location_from)
        if (
            len(location_to_check) > 0 and not LocationManager().is_allowed(
                user,
                location_to_check
            )
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = IndividualService(user)
        if IndividualConfig.check_individual_update:
            result = service.create_update_task(data)
        else:
            result = service.update(data)
        return result if not result['success'] else None

    class Input(UpdateIndividualInputType):
        pass


class DeleteIndividualMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeleteIndividualMutation"
    _mutation_module = "individual"
    _model = Individual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_individual_delete_perms):
            raise PermissionDenied(_("unauthorized"))

        locations_id = list(
            Location.objects.filter(
                individuals__id__in=data['ids'],
                *Location.filter_validity()
            ).values_list('id', flat=True)
        )
        if len(locations_id) > 0 and not LocationManager().is_allowed(
                user,
                locations_id
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = IndividualService(user)

        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for identifier in ids:
                    obj_data = {'id': identifier}
                    if IndividualConfig.check_individual_delete:
                        service.create_delete_task(obj_data)
                    else:
                        service.delete(obj_data)

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class UndoDeleteIndividualMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "UndoDeleteIndividualMutation"
    _mutation_module = "individual"
    _model = Individual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_individual_undo_delete_perms):
            raise PermissionDenied(_("unauthorized"))

        locations_id = list(
            Location.objects.filter(
                individuals__id__in=data['ids'],
                *Location.filter_validity()
            ).values_list('id', flat=True)
        )
        if len(locations_id) > 0 and not LocationManager().is_allowed(
                user,
                locations_id
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = IndividualService(user)

        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for identifier in ids:
                    service.undo_delete({'id': identifier})

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class CreateGroupMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateGroupMutation"
    _mutation_module = "individual"
    _model = Group

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_create_perms):
            raise PermissionDenied(_("unauthorized"))
        if (
            'location_id' in data and not LocationManager().is_allowed(
                user,
                [data['location_id']]
            )
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupService(user)
        result = service.create(data)
        return result if not result['success'] else None

    class Input(CreateGroupInputType):
        pass


class UpdateGroupMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdateGroupMutation"
    _mutation_module = "individual"
    _model = Group

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_update_perms):
            raise PermissionDenied(_("unauthorized"))
        location_from = Group.objects.get(id=data['id']).location_id
        location_to_check = [data['location_id']] if 'location_id' in data else []
        if location_from:
            location_to_check.append(location_from)
        if (
            len(location_to_check) > 0 and not LocationManager().is_allowed(
                user,
                location_to_check
            )
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupService(user)
        result = service.update(data)
        return result if not result['success'] else None

    class Input(UpdateGroupInputType):
        pass


class DeleteGroupMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeleteGroupMutation"
    _mutation_module = "individual"
    _model = Group

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_delete_perms):
            raise PermissionDenied(_("unauthorized"))

        locations_id = list(
            Location.objects.filter(
                groups__id__in=data['ids'],
                *Location.filter_validity()
            ).values_list('id', flat=True)
        )
        if len(locations_id) > 0 and not LocationManager().is_allowed(
                user,
                locations_id
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupService(user)

        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for identifier in ids:
                    obj_data = {'id': identifier}
                    if IndividualConfig.check_group_delete:
                        service.create_delete_task(obj_data)
                    else:
                        service.delete(obj_data)

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class CreateGroupIndividualMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateGroupIndividualMutation"
    _mutation_module = "individual"
    _model = GroupIndividual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_create_perms):
            raise PermissionDenied(_("unauthorized"))
        group_location_id = Group.objects.get(id=data['group_id']).location_id
        individual_location_id = Individual.objects.get(id=data['individual_id']).location_id
        location_to_check = []
        if group_location_id:
            location_to_check.append(group_location_id)
        if individual_location_id:
            location_to_check.append(individual_location_id)
        if len(location_to_check) > 0 and not LocationManager().is_allowed(
                user,
                location_to_check
        ):
            raise PermissionDenied(_("unauthorized.location"))

        if group_location_id and individual_location_id and group_location_id != individual_location_id:
            raise ValidationError(_("mutation.individual_group_location_mismatch"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupIndividualService(user)
        result = service.create(data)
        return result if not result['success'] else None

    class Input(CreateGroupIndividualInputType):
        pass


class UpdateGroupIndividualMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdateGroupIndividualMutation"
    _mutation_module = "individual"
    _model = GroupIndividual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_update_perms):
            raise PermissionDenied(_("unauthorized"))

        group_location_id = Group.objects.get(id=data['group_id']).location_id
        individual_location_id = Individual.objects.get(id=data['individual_id']).location_id
        location_to_check = []
        if individual_location_id:
            location_to_check.append(individual_location_id)
        if group_location_id:
            location_to_check.append(group_location_id)
        if len(location_to_check) > 0 and not LocationManager().is_allowed(
                user,
                location_to_check
        ):
            raise PermissionDenied(_("unauthorized.location"))

        if group_location_id and individual_location_id and group_location_id != individual_location_id:
            raise ValidationError(_("mutation.individual_group_location_mismatch"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupIndividualService(user)
        if IndividualConfig.check_group_individual_update:
            result = service.create_update_task(data)
        else:
            result = service.update(data)
        return result if not result['success'] else None

    class Input(UpdateGroupIndividualInputType):
        pass


class DeleteGroupIndividualMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeleteGroupIndividualMutation"
    _mutation_module = "individual"
    _model = GroupIndividual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_delete_perms):
            raise PermissionDenied(_("unauthorized"))
        locations_qs = list(
            Location.objects.filter(
                Q(groups__groupindividuals__id__in=data['ids']) | Q(
                    individuals__groupindividuals__id__in=data['ids'])
            ).filter(*Location.filter_validity()).values_list('id', flat=True)
        )
        # must first check if locations_qs exists in case none of the groups or individuals has location
        if len(locations_qs) > 0 and not LocationManager().is_allowed(
                user,
                locations_qs
        ):
            raise PermissionDenied(_("unauthorized.location"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupIndividualService(user)

        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for identifier in ids:
                    service.delete({'id': identifier})

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class CreateGroupIndividualsMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateGroupIndividualsMutation"
    _mutation_module = "individual"
    _model = Group

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_create_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = GroupService(user)
        result = service.create_group_individuals(data)
        return result if not result['success'] else None

    class Input(CreateGroupInputType):
        individual_ids = graphene.List(graphene.UUID, required=True)


class CreateGroupAndMoveIndividualMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateGroupAndMoveIndividualMutation"
    _mutation_module = "individual"
    _model = Group

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)

        required_perms = IndividualConfig.gql_group_create_perms + IndividualConfig.gql_group_update_perms
        if not user.has_perms(required_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = CreateGroupAndMoveIndividualService(user)
        if IndividualConfig.check_group_individual_update or IndividualConfig.check_group_create:
            result = service.create_create_task(data)
        else:
            result = service.create(data)
        return result if not result['success'] else None

    class Input(CreateGroupInputType):
        group_individual_id = graphene.UUID(required=True)


class ConfirmIndividualEnrollmentMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "ConfirmIndividualEnrollmentMutation"
    _mutation_module = "individual"
    _model = Individual

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_create_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')
        custom_filters = data.pop('custom_filters', None)
        benefit_plan_id = data.pop('benefit_plan_id', None)
        status = data.pop('status', "ACTIVE")
        service = IndividualService(user)
        service.select_individuals_to_benefit_plan(
            custom_filters,
            benefit_plan_id,
            status,
            user,
        )
        return None

    class Input(ConfirmIndividualEnrollmentInputType):
        pass


class ConfirmGroupEnrollmentMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "ConfirmGroupEnrollmentMutation"
    _mutation_module = "individual"
    _model = Group

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                IndividualConfig.gql_group_create_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')
        custom_filters = data.pop('custom_filters', None)
        benefit_plan_id = data.pop('benefit_plan_id', None)
        status = data.pop('status', "ACTIVE")
        service = GroupService(user)
        service.select_groups_to_benefit_plan(
            custom_filters,
            benefit_plan_id,
            status,
            user,
        )
        return None

    class Input(ConfirmIndividualEnrollmentInputType):
        pass
