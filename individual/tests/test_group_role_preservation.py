from django.test import TestCase

from core.test_helpers import LogInHelper
from individual.models import Group, GroupIndividual, Individual
from individual.services import GroupService


class GroupRolePreservationTest(TestCase):
    """A member's declared role must survive the group being assembled around it.

    Members are created one at a time and every save runs
    GroupAndGroupIndividualAlignmentService. While the group is still incomplete it has
    no head, so `_assure_primary_recipient_in_group` used to promote whichever member was
    created first to HEAD, overwriting the role that member arrived with. When the real
    head was created moments later, `_change_head` set that member's role to None - the
    original role was already gone.

    Nothing reported it. On a bulk import of 200 records into 50 groups this silently
    stripped the relationship from one member in most of them, and left the primary
    recipient - who gets paid - on an arbitrary member rather than the head.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()

    def _group_with(self, members):
        """Create a group whose members are supplied in the given order."""
        individuals_data = []
        for name, role, recipient in members:
            individual = Individual(first_name=name, last_name="Test", dob="1990-01-01")
            individual.save(user=self.user)
            individuals_data.append({"individual_id": str(individual.id),
                                     "role": role, "recipient_type": recipient})
        result = GroupService(self.user).create(
            {"code": f"ROLE-TEST-{len(Group.objects.all())}",
             "individuals_data": individuals_data})
        return result["data"]["id"]

    def _roles(self, group_id):
        return {gi.individual.first_name: (gi.role, gi.recipient_type)
                for gi in GroupIndividual.objects.filter(group_id=group_id, is_deleted=False)}

    def test_roles_survive_when_the_head_is_created_last(self):
        group_id = self._group_with([
            ("Child", GroupIndividual.Role.SON, None),
            ("Parent", GroupIndividual.Role.HEAD, GroupIndividual.RecipientType.PRIMARY),
        ])
        roles = self._roles(group_id)
        self.assertEqual(roles["Child"][0], GroupIndividual.Role.SON,
                         "the first-created member lost its declared role")
        self.assertEqual(roles["Parent"][0], GroupIndividual.Role.HEAD)

    def test_primary_recipient_ends_on_the_head_not_the_first_member(self):
        group_id = self._group_with([
            ("Child", GroupIndividual.Role.SON, None),
            ("Parent", GroupIndividual.Role.HEAD, GroupIndividual.RecipientType.PRIMARY),
        ])
        roles = self._roles(group_id)
        self.assertEqual(roles["Parent"][1], GroupIndividual.RecipientType.PRIMARY)
        self.assertIsNone(roles["Child"][1])

    def test_head_created_first_is_unaffected(self):
        group_id = self._group_with([
            ("Parent", GroupIndividual.Role.HEAD, GroupIndividual.RecipientType.PRIMARY),
            ("Child", GroupIndividual.Role.SON, None),
        ])
        roles = self._roles(group_id)
        self.assertEqual(roles["Parent"][0], GroupIndividual.Role.HEAD)
        self.assertEqual(roles["Child"][0], GroupIndividual.Role.SON)

    def test_group_with_no_head_still_gets_a_primary_recipient(self):
        """The safety net stays: someone must be able to receive on the group's behalf."""
        group_id = self._group_with([
            ("Cousin", GroupIndividual.Role.OTHER_RELATIVE, None),
            ("Nephew", GroupIndividual.Role.OTHER_RELATIVE, None),
        ])
        recipients = [r for _, r in self._roles(group_id).values()]
        self.assertEqual(recipients.count(GroupIndividual.RecipientType.PRIMARY), 1)

    def test_a_member_with_no_role_can_still_be_appointed_head(self):
        group_id = self._group_with([("Nobody", None, None)])
        roles = self._roles(group_id)
        self.assertEqual(roles["Nobody"][0], GroupIndividual.Role.HEAD)
        self.assertEqual(roles["Nobody"][1], GroupIndividual.RecipientType.PRIMARY)
