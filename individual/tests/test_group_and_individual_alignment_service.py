from django.test import TestCase

from core.test_helpers import LogInHelper
from individual.models import Individual, GroupIndividual, Group
from individual.services import GroupAndGroupIndividualAlignmentService
from individual.tests.test_helpers import (
    add_individual_to_group,
    create_individual,
    create_group,
)
from location.test_helpers import create_test_village


class GroupAndGroupIndividualAlignmentServiceTest(TestCase):
    user = None
    service = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.user = LogInHelper().get_or_create_user_api()
        cls.username = cls.user.username
        cls.service = GroupAndGroupIndividualAlignmentService(cls.user)

        cls.loc_a = create_test_village({
            'name': 'Village A',
            'code': 'ViaA',
        })

        cls.loc_b = create_test_village({
            'name': 'Village B',
            'code': 'ViaB'
        })

    def setUp(self):
        self.group = create_group(self.username)
        self.individual = create_individual(self.username)

    def assert_group_and_individual_location_equal(self, group_id, individual_id, location_id):
        group = Group.objects.get(id=group_id)
        individual = Individual.objects.get(id=individual_id)
        self.assertEqual(group.location_id, location_id)
        self.assertEqual(individual.location_id, location_id)

    def test_ensure_location_consistent_for_head(self):
        role = GroupIndividual.Role.HEAD

        # When group loc = head loc = None, no op
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assert_group_and_individual_location_equal(
            self.group.id, self.individual.id, None
        )

        # When group has no loc, head has loc, group takes location of the head
        self.individual.location = self.loc_a
        self.individual.save(user=self.user)
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assert_group_and_individual_location_equal(
            self.group.id, self.individual.id, self.loc_a.id
        )

        # When head has no loc, group has loc, head takes location of the group
        self.individual.location = None
        self.individual.save(user=self.user)
        self.group.location = self.loc_b
        self.group.save(user=self.user)
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assert_group_and_individual_location_equal(
            self.group.id, self.individual.id, self.loc_b.id
        )

        # When group and head have diff loc, update head to use group loc
        self.individual.location = self.loc_a
        self.individual.save(user=self.user)
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assert_group_and_individual_location_equal(
            self.group.id, self.individual.id, self.loc_b.id
        )

    def test_ensure_location_consistent_for_non_head(self):
        role = None

        # When group loc = non-head loc = None, no op
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assert_group_and_individual_location_equal(
            self.group.id, self.individual.id, None
        )

        # When the group has no loc yet, the non-head keeps its own - it cannot give the
        # group a location, and blanking its own would only destroy valid data
        self.individual.location = self.loc_a
        self.individual.save(user=self.user)
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assertIsNone(Group.objects.get(id=self.group.id).location_id)
        self.assertEqual(Individual.objects.get(id=self.individual.id).location_id, self.loc_a.id)

        # Once the group has a loc, individual loc takes group loc
        self.group.location = self.loc_b
        self.group.save(user=self.user)
        self.service.ensure_location_consistent(self.group, self.individual, role)
        self.assert_group_and_individual_location_equal(
            self.group.id, self.individual.id, self.loc_b.id
        )

    def test_member_joining_household_without_head_keeps_its_location(self):
        # The path a bulk import takes: members are added to a group whose head has not
        # been (or never will be) added, e.g. because the head's record was rejected.
        self.individual.location = self.loc_a
        self.individual.save(user=self.user)

        add_individual_to_group(self.username, self.individual, self.group, is_head=False)

        self.assertEqual(Individual.objects.get(id=self.individual.id).location_id, self.loc_a.id,
                         "a member of a household with no location lost its own location")

